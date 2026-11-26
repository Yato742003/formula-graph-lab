use std::alloc::{alloc, dealloc, Layout};
use std::f32::consts::TAU;

/// Compute unified 2D hierarchical lane coordinates with variable-height
/// overlap-free packing, balanced sub-column folding, and barycenter ordering.
///
/// Output buffers:
/// - `out_node_xy`: `node_count * 2` floats (`[x0, y0, x1, y1, ...]`) in canvas space.
/// - `out_group_rects`: `group_count * 4` floats (`[x, y, width, height, ...]`).
#[allow(clippy::too_many_arguments)]
pub fn compute_layout_2d(
    node_group_ids: &[u32],
    node_kind_ranks: &[u32],
    node_widths: &[f32],
    node_heights: &[f32],
    edge_srcs: &[u32],
    edge_dsts: &[u32],
    edge_weights: &[f32],
    group_count: usize,
    group_max_cols: &[u32],
    group_barycenter_flags: &[u32],
    params: &[f32; 8],
    out_node_xy: &mut [f32],
    out_group_rects: &mut [f32],
) {
    let n = node_group_ids.len();
    if n == 0 || group_count == 0 {
        return;
    }

    let lane_pad_x = params[0].max(8.0);
    let lane_pad_top = params[1].max(24.0);
    let lane_pad_bottom = params[2].max(12.0);
    let card_gap_x = params[3].max(12.0);
    let card_gap_y = params[4].max(10.0);
    let lane_gap_x = params[5].max(24.0);
    let lane_gap_y = params[6].max(24.0);
    let max_lanes_per_row = (params[7].round() as usize).max(1);

    let mut groups: Vec<Vec<usize>> = vec![Vec::new(); group_count];
    for (idx, &gid) in node_group_ids.iter().enumerate() {
        let g = (gid as usize).min(group_count - 1);
        groups[g].push(idx);
    }

    // Initial deterministic order inside each lane: (kind_rank, original_index).
    for members in &mut groups {
        members.sort_by_key(|&i| (node_kind_ranks[i], i));
    }

    // Build cross-lane adjacency for Sugiyama barycenter refinement on flexible lanes
    // (preserving strict reading order inside numbered equation sections).
    let edge_len = edge_srcs.len().min(edge_dsts.len()).min(edge_weights.len());
    let mut adj: Vec<Vec<(usize, f32)>> = vec![Vec::new(); n];
    for e in 0..edge_len {
        let u = edge_srcs[e] as usize;
        let v = edge_dsts[e] as usize;
        if u < n && v < n && u != v {
            let w = edge_weights[e].max(0.25);
            adj[u].push((v, w));
            adj[v].push((u, w));
        }
    }

    // Two passes of barycenter sorting for lanes flagged as reorderable.
    let mut rank_pos = vec![0.0f32; n];
    for _pass in 0..2 {
        for members in &groups {
            let len = members.len().max(1) as f32;
            for (pos, &node_idx) in members.iter().enumerate() {
                rank_pos[node_idx] = (pos as f32 + 0.5) / len;
            }
        }
        for (g, members) in groups.iter_mut().enumerate() {
            let allow_bary = group_barycenter_flags.get(g).copied().unwrap_or(0) != 0;
            if !allow_bary || members.len() <= 2 {
                continue;
            }
            let mut scored: Vec<(usize, u32, f32, usize)> = members
                .iter()
                .enumerate()
                .map(|(orig_pos, &node_idx)| {
                    let mut sum = 0.0f32;
                    let mut wsum = 0.0f32;
                    for &(nb, w) in &adj[node_idx] {
                        if node_group_ids[nb] as usize != g {
                            sum += rank_pos[nb] * w;
                            wsum += w;
                        }
                    }
                    let bary = if wsum > 0.0 {
                        sum / wsum
                    } else {
                        (orig_pos as f32 + 0.5) / (members.len() as f32)
                    };
                    (node_idx, node_kind_ranks[node_idx], bary, orig_pos)
                })
                .collect();
            scored.sort_by(|a, b| {
                a.1.cmp(&b.1)
                    .then_with(|| a.2.total_cmp(&b.2))
                    .then_with(|| a.3.cmp(&b.3))
            });
            for (dst, item) in members.iter_mut().zip(scored.into_iter()) {
                *dst = item.0;
            }
        }
    }

    // Measure and locally position cards inside each group using balanced sub-columns.
    struct LocalLane {
        group_id: usize,
        width: f32,
        height: f32,
        /// (node_idx, local_x, local_y)
        placements: Vec<(usize, f32, f32)>,
    }

    let mut local_lanes: Vec<LocalLane> = Vec::with_capacity(group_count);

    for (g, members) in groups.iter().enumerate() {
        if members.is_empty() {
            local_lanes.push(LocalLane {
                group_id: g,
                width: 0.0,
                height: 0.0,
                placements: Vec::new(),
            });
            continue;
        }

        let max_cols = group_max_cols.get(g).copied().unwrap_or(1).max(1) as usize;
        let cols = if members.len() <= 1 || max_cols == 1 {
            1
        } else {
            // Choose sub-columns so very tall lanes fold gracefully without creating
            // sparse 1-item columns.
            let desired = ((members.len() as f32) / 5.0).ceil() as usize;
            desired.clamp(1, max_cols).min(members.len())
        };

        // Partition sequentially into `cols` chunks balanced by estimated total height
        // so reading order stays top-to-bottom, left-to-right inside the lane.
        let total_h: f32 = members.iter().map(|&i| node_heights[i].max(44.0) + card_gap_y).sum();
        let target_col_h = (total_h / (cols as f32)).max(1.0);

        let mut col_buckets: Vec<Vec<usize>> = vec![Vec::new(); cols];
        let mut current_col = 0usize;
        let mut current_h = 0.0f32;
        for (pos, &node_idx) in members.iter().enumerate() {
            let item_h = node_heights[node_idx].max(44.0) + card_gap_y;
            let remaining_items = members.len() - pos;
            let remaining_cols = cols - current_col;
            if current_col + 1 < cols
                && !col_buckets[current_col].is_empty()
                && (current_h + item_h * 0.5 > target_col_h || remaining_items < remaining_cols)
            {
                current_col += 1;
                current_h = 0.0;
            }
            col_buckets[current_col].push(node_idx);
            current_h += item_h;
        }

        // Remove any trailing empty buckets cleanly.
        col_buckets.retain(|b| !b.is_empty());
        let actual_cols = col_buckets.len().max(1);

        let mut col_widths = vec![0.0f32; actual_cols];
        for (c, bucket) in col_buckets.iter().enumerate() {
            let mut max_w = 220.0f32;
            for &node_idx in bucket {
                max_w = max_w.max(node_widths[node_idx].max(160.0));
            }
            col_widths[c] = max_w;
        }

        let mut placements = Vec::with_capacity(members.len());
        let mut cursor_x = lane_pad_x;
        let mut max_col_bottom = lane_pad_top;

        for (c, bucket) in col_buckets.iter().enumerate() {
            let col_w = col_widths[c];
            let mut cursor_y = lane_pad_top;
            for &node_idx in bucket {
                let card_w = node_widths[node_idx].max(160.0);
                let card_h = node_heights[node_idx].max(44.0);
                let centered_x = cursor_x + ((col_w - card_w).max(0.0) * 0.5);
                placements.push((node_idx, centered_x, cursor_y));
                cursor_y += card_h + card_gap_y;
            }
            let col_bottom = (cursor_y - card_gap_y).max(lane_pad_top);
            max_col_bottom = max_col_bottom.max(col_bottom);
            cursor_x += col_w + card_gap_x;
        }

        let lane_w = (cursor_x - card_gap_x) + lane_pad_x;
        let lane_h = max_col_bottom + lane_pad_bottom;

        local_lanes.push(LocalLane {
            group_id: g,
            width: lane_w,
            height: lane_h,
            placements,
        });
    }

    // Group 0 is the Paper anchor lane on the left; groups 1..group_count are content lanes
    // packed into clean horizontal bands with tight per-band heights.
    let mut group_xy = vec![(0.0f32, 0.0f32); group_count];
    let has_root_lane = !local_lanes[0].placements.is_empty();
    let content_start_x = if has_root_lane {
        local_lanes[0].width + lane_gap_x + 28.0
    } else {
        0.0
    };

    let content_ids: Vec<usize> = (1..group_count)
        .filter(|&g| !local_lanes[g].placements.is_empty())
        .collect();

    let mut band_y = 0.0f32;
    let mut first_band_h = 0.0f32;
    for (band_idx, chunk) in content_ids.chunks(max_lanes_per_row).enumerate() {
        let mut cursor_x = content_start_x;
        let mut band_max_h = 0.0f32;
        for &g in chunk {
            group_xy[g] = (cursor_x, band_y);
            cursor_x += local_lanes[g].width + lane_gap_x;
            band_max_h = band_max_h.max(local_lanes[g].height);
        }
        if band_idx == 0 {
            first_band_h = band_max_h;
        }
        band_y += band_max_h + lane_gap_y;
    }

    if has_root_lane {
        let root_y = if first_band_h > local_lanes[0].height {
            ((first_band_h - local_lanes[0].height) * 0.25).max(0.0)
        } else {
            0.0
        };
        group_xy[0] = (0.0, root_y);
    }

    // Write output group rects and absolute node coordinates.
    for lane in &local_lanes {
        let g = lane.group_id;
        let (gx, gy) = group_xy[g];
        let base = g * 4;
        if base + 3 < out_group_rects.len() {
            out_group_rects[base] = gx;
            out_group_rects[base + 1] = gy;
            out_group_rects[base + 2] = lane.width;
            out_group_rects[base + 3] = lane.height;
        }
        for &(node_idx, lx, ly) in &lane.placements {
            let nbase = node_idx * 2;
            if nbase + 1 < out_node_xy.len() {
                out_node_xy[nbase] = gx + lx;
                out_node_xy[nbase + 1] = gy + ly;
            }
        }
    }
}

/// Compute 3D hierarchical scientific constellation layout with deterministic
/// short-range collision relaxation and epistemic elevation separation.
///
/// Kind codes:
/// - `0`: Paper / PaperVersion (spine anchors)
/// - `1`: Section (primary orbital hubs around owning Paper)
/// - `2`: Equation (outer shell inside parent Section's angular wedge)
/// - `3`: Symbol (tight satellites orbiting parent Equation)
/// - `4`: Concept / Method / Assumption (lower semantic support ring)
/// - `5`: Claim / Hypothesis (elevated epistemic halo above evidence plane)
///
/// Output buffer `out_xyzp`: `node_count * 4` floats (`[x, y, z, progress, ...]`).
pub fn compute_layout_3d(
    node_kinds: &[u32],
    node_parents: &[i32],
    node_root_papers: &[i32],
    edge_srcs: &[u32],
    edge_dsts: &[u32],
    edge_kind_codes: &[u32],
    out_xyzp: &mut [f32],
) {
    let n = node_kinds.len();
    if n == 0 || out_xyzp.len() < n * 4 {
        return;
    }

    let mut pos = vec![[0.0f32; 3]; n];
    let mut anchor = vec![[0.0f32; 3]; n];
    let mut progress = vec![0.5f32; n];

    // 1. Collect paper roots along the horizontal spine.
    let mut paper_indices: Vec<usize> = Vec::new();
    for (i, &k) in node_kinds.iter().enumerate() {
        if k == 0 {
            paper_indices.push(i);
        }
    }
    let paper_spacing = 36.0f32;
    let paper_count = paper_indices.len();
    let mut paper_pos_by_slot: Vec<[f32; 3]> = Vec::with_capacity(paper_count.max(1));

    if paper_count == 0 {
        paper_pos_by_slot.push([0.0, 0.0, 0.0]);
    } else {
        let half = (paper_count as f32 - 1.0) * 0.5;
        for (slot, &idx) in paper_indices.iter().enumerate() {
            let x = (slot as f32 - half) * paper_spacing;
            let p = [x, 0.0, 0.0];
            pos[idx] = p;
            anchor[idx] = p;
            progress[idx] = 0.03 + 0.08 * (slot as f32 / (paper_count as f32).max(1.0));
            paper_pos_by_slot.push(p);
        }
    }

    // Helper: fetch owning paper center.
    let root_center = |i: usize| -> [f32; 3] {
        let rp = node_root_papers.get(i).copied().unwrap_or(0);
        if rp >= 0 && (rp as usize) < paper_pos_by_slot.len() {
            paper_pos_by_slot[rp as usize]
        } else {
            paper_pos_by_slot[0]
        }
    };

    // 2. Group sections by owning paper slot; distinguish equation-bearing sections
    //    from empty text-only outline sections so large papers keep crystal-clear sightlines.
    let mut section_has_eq = vec![false; n];
    for (i, &k) in node_kinds.iter().enumerate() {
        if k == 2 {
            let p = node_parents.get(i).copied().unwrap_or(-1);
            if p >= 0 && (p as usize) < n && node_kinds[p as usize] == 1 {
                section_has_eq[p as usize] = true;
            }
        }
    }

    let mut primary_sections_per_paper: Vec<Vec<usize>> = vec![Vec::new(); paper_pos_by_slot.len()];
    let mut outline_sections_per_paper: Vec<Vec<usize>> = vec![Vec::new(); paper_pos_by_slot.len()];
    for (i, &k) in node_kinds.iter().enumerate() {
        if k == 1 {
            let slot = node_root_papers
                .get(i)
                .copied()
                .unwrap_or(0)
                .clamp(0, (paper_pos_by_slot.len() - 1) as i32) as usize;
            if section_has_eq[i] {
                primary_sections_per_paper[slot].push(i);
            } else {
                outline_sections_per_paper[slot].push(i);
            }
        }
    }

    let mut node_angle = vec![0.0f32; n];
    let mut section_wedge = vec![TAU / 6.0; n];

    for slot in 0..paper_pos_by_slot.len() {
        let center = paper_pos_by_slot[slot];
        let primary = &primary_sections_per_paper[slot];
        let outline = &outline_sections_per_paper[slot];

        // If no section owns equations, treat all sections as primary ring.
        let use_split = !primary.is_empty() && outline.len() >= 6;
        let ring_list: Vec<usize> = if use_split {
            primary.clone()
        } else {
            primary.iter().chain(outline.iter()).copied().collect()
        };

        let p_count = ring_list.len();
        if p_count > 0 {
            let ring_r = (11.0 + (p_count as f32).sqrt() * 1.35).min(15.5);
            let wedge = TAU / (p_count as f32);
            for (rank, &idx) in ring_list.iter().enumerate() {
                let theta = (rank as f32) * wedge + (slot as f32) * 0.45;
                let y = (theta * 2.0).sin() * 1.4;
                let p = [
                    center[0] + theta.cos() * ring_r,
                    center[1] + y,
                    center[2] + theta.sin() * ring_r,
                ];
                pos[idx] = p;
                anchor[idx] = p;
                node_angle[idx] = theta;
                section_wedge[idx] = wedge;
                progress[idx] = 0.14 + 0.22 * (rank as f32 / (p_count as f32).max(1.0));
            }
        }

        if use_split && !outline.is_empty() {
            let o_count = outline.len();
            let o_wedge = TAU / (o_count as f32);
            for (rank, &idx) in outline.iter().enumerate() {
                let theta = (rank as f32) * o_wedge + 0.22;
                let tier = (rank % 2) as f32;
                let r = 8.2 + tier * 3.4;
                let p = [
                    center[0] + theta.cos() * r,
                    center[1] - 6.2 - tier * 1.2,
                    center[2] + theta.sin() * r,
                ];
                pos[idx] = p;
                anchor[idx] = p;
                node_angle[idx] = theta;
                section_wedge[idx] = o_wedge;
                progress[idx] = 0.2 + 0.16 * (rank as f32 / (o_count as f32).max(1.0));
            }
        }
    }

    // 3. Place Equations inside their parent Section's angular wedge (or around Paper if flat).
    let mut eq_by_parent: Vec<(i32, Vec<usize>)> = Vec::new();
    for (i, &k) in node_kinds.iter().enumerate() {
        if k == 2 {
            let parent = node_parents.get(i).copied().unwrap_or(-1);
            if let Some((_, list)) = eq_by_parent.iter_mut().find(|(p, _)| *p == parent) {
                list.push(i);
            } else {
                eq_by_parent.push((parent, vec![i]));
            }
        }
    }

    for (parent_id, eq_list) in &eq_by_parent {
        let count = eq_list.len();
        let has_section_parent = *parent_id >= 0
            && (*parent_id as usize) < n
            && node_kinds[*parent_id as usize] == 1;

        if has_section_parent {
            let p_idx = *parent_id as usize;
            let center = root_center(p_idx);
            let base_theta = node_angle[p_idx];
            let wedge = (section_wedge[p_idx] * 0.78).min(1.15);
            for (rank, &idx) in eq_list.iter().enumerate() {
                let tier = rank / 4;
                let in_tier = rank % 4;
                let tier_len = (count - tier * 4).min(4);
                let frac = if tier_len <= 1 {
                    0.0
                } else {
                    (in_tier as f32 / (tier_len as f32 - 1.0)) - 0.5
                };
                let theta = base_theta + frac * wedge;
                let r = 19.5 + (tier as f32) * 4.4;
                let y = pos[p_idx][1] + ((in_tier as f32) - (tier_len as f32 - 1.0) * 0.5) * 1.9;
                let p = [
                    center[0] + theta.cos() * r,
                    y,
                    center[2] + theta.sin() * r,
                ];
                pos[idx] = p;
                anchor[idx] = p;
                node_angle[idx] = theta;
                progress[idx] = 0.38 + 0.38 * (rank as f32 / (count as f32).max(1.0));
            }
        } else {
            // Equations attached directly to Paper (or ungrouped): clean double-helix orbital band.
            for (rank, &idx) in eq_list.iter().enumerate() {
                let center = root_center(idx);
                let frac = (rank as f32) / (count as f32).max(1.0);
                let theta = frac * TAU * if count > 10 { 2.0 } else { 1.0 } + 0.3;
                let ring = (rank / 10) as f32;
                let r = 16.5 + ring * 5.0;
                let y = ((rank % 5) as f32 - 2.0) * 1.85;
                let p = [
                    center[0] + theta.cos() * r,
                    center[1] + y,
                    center[2] + theta.sin() * r,
                ];
                pos[idx] = p;
                anchor[idx] = p;
                node_angle[idx] = theta;
                progress[idx] = 0.36 + 0.40 * frac;
            }
        }
    }

    // 4. Place Symbols as micro-satellites around their parent Equation (or outer ring if orphan).
    let mut sym_by_parent: Vec<(i32, Vec<usize>)> = Vec::new();
    for (i, &k) in node_kinds.iter().enumerate() {
        if k == 3 {
            let parent = node_parents.get(i).copied().unwrap_or(-1);
            if let Some((_, list)) = sym_by_parent.iter_mut().find(|(p, _)| *p == parent) {
                list.push(i);
            } else {
                sym_by_parent.push((parent, vec![i]));
            }
        }
    }

    for (parent_id, sym_list) in &sym_by_parent {
        let count = sym_list.len();
        let has_valid_parent = *parent_id >= 0 && (*parent_id as usize) < n;
        for (rank, &idx) in sym_list.iter().enumerate() {
            let frac = (rank as f32) / (count as f32).max(1.0);
            let p = if has_valid_parent {
                let p_idx = *parent_id as usize;
                let parent_p = pos[p_idx];
                let base_a = node_angle[p_idx];
                let phi = base_a + frac * TAU + 0.5;
                let sat_r = 3.2 + ((rank / 6) as f32) * 1.4;
                let sat_y = ((rank % 3) as f32 - 1.0) * 1.25;
                [
                    parent_p[0] + phi.cos() * sat_r,
                    parent_p[1] + sat_y,
                    parent_p[2] + phi.sin() * sat_r,
                ]
            } else {
                let center = root_center(idx);
                let theta = frac * TAU + 0.7;
                [
                    center[0] + theta.cos() * 23.5,
                    center[1] - 4.2,
                    center[2] + theta.sin() * 23.5,
                ]
            };
            pos[idx] = p;
            anchor[idx] = p;
            progress[idx] = 0.76 + 0.22 * frac;
        }
    }

    // 5. Place Concepts/Methods/Assumptions (kind 4, lower band) & Claims/Hypotheses (kind 5, upper epistemic halo).
    let mut lower_nodes = Vec::new();
    let mut upper_nodes = Vec::new();
    for (i, &k) in node_kinds.iter().enumerate() {
        if k == 4 {
            lower_nodes.push(i);
        } else if k >= 5 {
            upper_nodes.push(i);
        }
    }

    for (rank, &idx) in lower_nodes.iter().enumerate() {
        let center = root_center(idx);
        let frac = (rank as f32) / (lower_nodes.len() as f32).max(1.0);
        let theta = frac * TAU + 0.25;
        let r = 14.5 + ((rank % 2) as f32) * 4.5;
        let p = [
            center[0] + theta.cos() * r,
            center[1] - 7.2 - ((rank % 3) as f32) * 0.9,
            center[2] + theta.sin() * r,
        ];
        pos[idx] = p;
        anchor[idx] = p;
        progress[idx] = 0.62 + 0.28 * frac;
    }

    for (rank, &idx) in upper_nodes.iter().enumerate() {
        let center = root_center(idx);
        let frac = (rank as f32) / (upper_nodes.len() as f32).max(1.0);
        let theta = frac * TAU + 0.85;
        let r = 13.5 + ((rank % 2) as f32) * 4.2;
        let p = [
            center[0] + theta.cos() * r,
            center[1] + 8.0 + ((rank % 3) as f32) * 1.1,
            center[2] + theta.sin() * r,
        ];
        pos[idx] = p;
        anchor[idx] = p;
        progress[idx] = 0.74 + 0.24 * frac;
    }

    // 6. Deterministic 3D force relaxation: resolves pairwise overlaps while preserving
    //    hierarchical constellation readability (papers stay pinned).
    let edge_len = edge_srcs
        .len()
        .min(edge_dsts.len())
        .min(edge_kind_codes.len());
    let mut disp = vec![[0.0f32; 3]; n];
    let iterations = if n > 400 { 24 } else { 44 };

    let min_sep_for = |kind: u32| -> f32 {
        match kind {
            0 => 6.0,
            1 => 4.4,
            2 => 3.5,
            3 => 2.1,
            _ => 3.2,
        }
    };

    for iter in 0..iterations {
        for d in &mut disp {
            *d = [0.0, 0.0, 0.0];
        }
        let step_scale = 0.42 * (1.0 - (iter as f32 / (iterations as f32 + 8.0)));

        // Pairwise short-range collision avoidance.
        for i in 0..n {
            let sep_i = min_sep_for(node_kinds[i]);
            for j in (i + 1)..n {
                let target_sep = (sep_i + min_sep_for(node_kinds[j])) * 0.5;
                let dx = pos[i][0] - pos[j][0];
                let dy = pos[i][1] - pos[j][1];
                let dz = pos[i][2] - pos[j][2];
                let dist_sq = dx * dx + dy * dy + dz * dz;
                let max_sq = target_sep * target_sep;
                if dist_sq < max_sq {
                    let (nx, ny, nz, dist) = if dist_sq > 1e-6 {
                        let d = dist_sq.sqrt();
                        (dx / d, dy / d, dz / d, d)
                    } else {
                        // Deterministic tie-breaker when two nodes overlap exactly.
                        let angle = ((i * 73 + j * 37) % 360) as f32 * (TAU / 360.0);
                        (angle.cos(), 0.25, angle.sin(), 0.01)
                    };
                    let push = (target_sep - dist) * 0.48;
                    disp[i][0] += nx * push;
                    disp[i][1] += ny * push;
                    disp[i][2] += nz * push;
                    disp[j][0] -= nx * push;
                    disp[j][1] -= ny * push;
                    disp[j][2] -= nz * push;
                }
            }
        }

        // Gentle semantic attraction along derivation / citation edges.
        for e in 0..edge_len {
            let u = edge_srcs[e] as usize;
            let v = edge_dsts[e] as usize;
            let code = edge_kind_codes[e];
            if u < n && v < n && u != v && (code == 1 || code == 4) {
                let dx = pos[v][0] - pos[u][0];
                let dy = pos[v][1] - pos[u][1];
                let dz = pos[v][2] - pos[u][2];
                let dist = (dx * dx + dy * dy + dz * dz).sqrt().max(0.01);
                let ideal = 7.5f32;
                if dist > ideal {
                    let pull = (dist - ideal).min(6.0) * 0.035;
                    let fx = (dx / dist) * pull;
                    let fy = (dy / dist) * pull;
                    let fz = (dz / dist) * pull;
                    disp[u][0] += fx;
                    disp[u][1] += fy;
                    disp[u][2] += fz;
                    disp[v][0] -= fx;
                    disp[v][1] -= fy;
                    disp[v][2] -= fz;
                }
            }
        }

        // Anchor tether spring keeps clusters faithful to their hierarchical sector.
        for i in 0..n {
            if node_kinds[i] == 0 {
                continue; // Pin root Paper nodes on the observatory spine.
            }
            let tether = if node_kinds[i] == 1 { 0.26 } else { 0.14 };
            disp[i][0] += (anchor[i][0] - pos[i][0]) * tether;
            disp[i][1] += (anchor[i][1] - pos[i][1]) * tether;
            disp[i][2] += (anchor[i][2] - pos[i][2]) * tether;

            let mag = (disp[i][0] * disp[i][0] + disp[i][1] * disp[i][1] + disp[i][2] * disp[i][2])
                .sqrt();
            let max_step = 2.4f32;
            let clamp = if mag > max_step { max_step / mag } else { 1.0 };
            pos[i][0] += disp[i][0] * clamp * step_scale;
            pos[i][1] += disp[i][1] * clamp * step_scale;
            pos[i][2] += disp[i][2] * clamp * step_scale;
        }
    }

    // Center the constellation around origin for balanced 3D orbit framing.
    let mut min_b = [f32::INFINITY; 3];
    let mut max_b = [f32::NEG_INFINITY; 3];
    for p in &pos {
        for axis in 0..3 {
            min_b[axis] = min_b[axis].min(p[axis]);
            max_b[axis] = max_b[axis].max(p[axis]);
        }
    }
    let mid = [
        (min_b[0] + max_b[0]) * 0.5,
        (min_b[1] + max_b[1]) * 0.5,
        (min_b[2] + max_b[2]) * 0.5,
    ];

    for i in 0..n {
        let base = i * 4;
        out_xyzp[base] = pos[i][0] - mid[0];
        out_xyzp[base + 1] = pos[i][1] - mid[1];
        out_xyzp[base + 2] = pos[i][2] - mid[2];
        out_xyzp[base + 3] = progress[i].clamp(0.02, 1.0);
    }
}

// ---------- WASM Linear Memory FFI ----------

#[no_mangle]
pub extern "C" fn wasm_alloc(byte_len: usize) -> *mut u8 {
    let size = byte_len.max(1);
    let Ok(layout) = Layout::from_size_align(size, 4) else {
        return std::ptr::null_mut();
    };
    // SAFETY: `layout` has non-zero size and 4-byte alignment matching u32/f32 buffers.
    unsafe { alloc(layout) }
}

#[no_mangle]
pub extern "C" fn wasm_dealloc(ptr: *mut u8, byte_len: usize) {
    if ptr.is_null() {
        return;
    }
    let size = byte_len.max(1);
    if let Ok(layout) = Layout::from_size_align(size, 4) {
        // SAFETY: caller passes pointer/length previously returned by `wasm_alloc`.
        unsafe { dealloc(ptr, layout) };
    }
}

/// FFI entry point for 2D layout. Returns 1 on success, 0 on invalid arguments.
#[no_mangle]
#[allow(clippy::too_many_arguments)]
pub extern "C" fn wasm_layout_2d(
    node_count: usize,
    group_count: usize,
    edge_count: usize,
    node_group_ids_ptr: *const u32,
    node_kind_ranks_ptr: *const u32,
    node_widths_ptr: *const f32,
    node_heights_ptr: *const f32,
    edge_srcs_ptr: *const u32,
    edge_dsts_ptr: *const u32,
    edge_weights_ptr: *const f32,
    group_max_cols_ptr: *const u32,
    group_bary_flags_ptr: *const u32,
    params_ptr: *const f32,
    out_node_xy_ptr: *mut f32,
    out_group_rects_ptr: *mut f32,
) -> u32 {
    if node_count == 0
        || group_count == 0
        || node_group_ids_ptr.is_null()
        || node_kind_ranks_ptr.is_null()
        || node_widths_ptr.is_null()
        || node_heights_ptr.is_null()
        || group_max_cols_ptr.is_null()
        || group_bary_flags_ptr.is_null()
        || params_ptr.is_null()
        || out_node_xy_ptr.is_null()
        || out_group_rects_ptr.is_null()
    {
        return 0;
    }

    // SAFETY: Caller allocates exact typed arrays in WASM linear memory via `wasm_alloc`.
    unsafe {
        let node_group_ids = std::slice::from_raw_parts(node_group_ids_ptr, node_count);
        let node_kind_ranks = std::slice::from_raw_parts(node_kind_ranks_ptr, node_count);
        let node_widths = std::slice::from_raw_parts(node_widths_ptr, node_count);
        let node_heights = std::slice::from_raw_parts(node_heights_ptr, node_count);
        let (edge_srcs, edge_dsts, edge_weights) = if edge_count > 0
            && !edge_srcs_ptr.is_null()
            && !edge_dsts_ptr.is_null()
            && !edge_weights_ptr.is_null()
        {
            (
                std::slice::from_raw_parts(edge_srcs_ptr, edge_count),
                std::slice::from_raw_parts(edge_dsts_ptr, edge_count),
                std::slice::from_raw_parts(edge_weights_ptr, edge_count),
            )
        } else {
            (&[][..], &[][..], &[][..])
        };
        let group_max_cols = std::slice::from_raw_parts(group_max_cols_ptr, group_count);
        let group_bary_flags = std::slice::from_raw_parts(group_bary_flags_ptr, group_count);
        let params_slice = std::slice::from_raw_parts(params_ptr, 8);
        let mut params = [0.0f32; 8];
        params.copy_from_slice(params_slice);
        let out_node_xy = std::slice::from_raw_parts_mut(out_node_xy_ptr, node_count * 2);
        let out_group_rects = std::slice::from_raw_parts_mut(out_group_rects_ptr, group_count * 4);

        compute_layout_2d(
            node_group_ids,
            node_kind_ranks,
            node_widths,
            node_heights,
            edge_srcs,
            edge_dsts,
            edge_weights,
            group_count,
            group_max_cols,
            group_bary_flags,
            &params,
            out_node_xy,
            out_group_rects,
        );
    }
    1
}

/// FFI entry point for 3D constellation layout. Returns 1 on success, 0 on invalid arguments.
#[no_mangle]
#[allow(clippy::too_many_arguments)]
pub extern "C" fn wasm_layout_3d(
    node_count: usize,
    edge_count: usize,
    node_kinds_ptr: *const u32,
    node_parents_ptr: *const i32,
    node_root_papers_ptr: *const i32,
    edge_srcs_ptr: *const u32,
    edge_dsts_ptr: *const u32,
    edge_kind_codes_ptr: *const u32,
    out_xyzp_ptr: *mut f32,
) -> u32 {
    if node_count == 0
        || node_kinds_ptr.is_null()
        || node_parents_ptr.is_null()
        || node_root_papers_ptr.is_null()
        || out_xyzp_ptr.is_null()
    {
        return 0;
    }

    // SAFETY: Caller allocates exact typed arrays in WASM linear memory via `wasm_alloc`.
    unsafe {
        let node_kinds = std::slice::from_raw_parts(node_kinds_ptr, node_count);
        let node_parents = std::slice::from_raw_parts(node_parents_ptr, node_count);
        let node_root_papers = std::slice::from_raw_parts(node_root_papers_ptr, node_count);
        let (edge_srcs, edge_dsts, edge_kind_codes) = if edge_count > 0
            && !edge_srcs_ptr.is_null()
            && !edge_dsts_ptr.is_null()
            && !edge_kind_codes_ptr.is_null()
        {
            (
                std::slice::from_raw_parts(edge_srcs_ptr, edge_count),
                std::slice::from_raw_parts(edge_dsts_ptr, edge_count),
                std::slice::from_raw_parts(edge_kind_codes_ptr, edge_count),
            )
        } else {
            (&[][..], &[][..], &[][..])
        };
        let out_xyzp = std::slice::from_raw_parts_mut(out_xyzp_ptr, node_count * 4);

        compute_layout_3d(
            node_kinds,
            node_parents,
            node_root_papers,
            edge_srcs,
            edge_dsts,
            edge_kind_codes,
            out_xyzp,
        );
    }
    1
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn layout_2d_packs_variable_height_cards_without_overlap() {
        let group_ids = [0, 1, 1, 1, 2, 2];
        let kind_ranks = [0, 2, 2, 2, 5, 5];
        let widths = [280.0, 320.0, 320.0, 320.0, 320.0, 320.0];
        let heights = [148.0, 140.0, 248.0, 196.0, 160.0, 210.0];
        let max_cols = [1, 1, 1];
        let bary_flags = [0, 0, 1];
        let params = [24.0, 68.0, 24.0, 24.0, 20.0, 56.0, 48.0, 3.0];

        let mut node_xy = vec![0.0f32; group_ids.len() * 2];
        let mut group_rects = vec![0.0f32; 3 * 4];

        compute_layout_2d(
            &group_ids,
            &kind_ranks,
            &widths,
            &heights,
            &[1, 2],
            &[4, 5],
            &[2.0, 2.0],
            3,
            &max_cols,
            &bary_flags,
            &params,
            &mut node_xy,
            &mut group_rects,
        );

        // Assert pairwise non-overlap across all 6 variable-height cards.
        for i in 0..group_ids.len() {
            let (xi, yi, wi, hi) = (node_xy[i * 2], node_xy[i * 2 + 1], widths[i], heights[i]);
            for j in (i + 1)..group_ids.len() {
                let (xj, yj, wj, hj) = (node_xy[j * 2], node_xy[j * 2 + 1], widths[j], heights[j]);
                let overlap_x = xi < xj + wj && xi + wi > xj;
                let overlap_y = yi < yj + hj && yi + hi > yj;
                assert!(
                    !(overlap_x && overlap_y),
                    "Nodes {i} and {j} overlap: ({xi},{yi},{wi},{hi}) vs ({xj},{yj},{wj},{hj})"
                );
            }
        }

        // Assert every card stays strictly inside its lane bounding box.
        for i in 0..group_ids.len() {
            let g = group_ids[i] as usize;
            let (gx, gy, gw, gh) = (
                group_rects[g * 4],
                group_rects[g * 4 + 1],
                group_rects[g * 4 + 2],
                group_rects[g * 4 + 3],
            );
            let (xi, yi, wi, hi) = (node_xy[i * 2], node_xy[i * 2 + 1], widths[i], heights[i]);
            assert!(xi >= gx && xi + wi <= gx + gw + 0.01);
            assert!(yi >= gy && yi + hi <= gy + gh + 0.01);
        }
    }

    #[test]
    fn layout_2d_folds_24_sections_into_multiple_subcolumns() {
        let mut group_ids = vec![0u32];
        let mut kind_ranks = vec![0u32];
        let mut widths = vec![280.0f32];
        let mut heights = vec![140.0f32];
        for _ in 0..24 {
            group_ids.push(1);
            kind_ranks.push(1);
            widths.push(220.0);
            heights.push(48.0);
        }
        let max_cols = [1, 3];
        let bary_flags = [0, 0];
        let params = [24.0, 68.0, 24.0, 20.0, 14.0, 48.0, 40.0, 3.0];

        let mut node_xy = vec![0.0f32; group_ids.len() * 2];
        let mut group_rects = vec![0.0f32; 2 * 4];
        compute_layout_2d(
            &group_ids,
            &kind_ranks,
            &widths,
            &heights,
            &[],
            &[],
            &[],
            2,
            &max_cols,
            &bary_flags,
            &params,
            &mut node_xy,
            &mut group_rects,
        );

        let lane_h = group_rects[7];
        // 24 compact items folded into 3 sub-columns should stay well under 750px tall.
        assert!(lane_h < 750.0, "Expected folded lane height < 750, got {lane_h}");
    }

    #[test]
    fn layout_3d_builds_overlap_free_hierarchical_constellation() {
        // 1 Paper, 3 Sections, 9 Equations, 9 Symbols, 2 Hypotheses
        let mut kinds = vec![0u32];
        let mut parents = vec![-1i32];
        let mut roots = vec![0i32];

        for _ in 0..3 {
            kinds.push(1);
            parents.push(0);
            roots.push(0);
        }
        for eq in 0..9 {
            kinds.push(2);
            parents.push(1 + (eq % 3));
            roots.push(0);
        }
        for sym in 0..9 {
            kinds.push(3);
            parents.push(4 + sym);
            roots.push(0);
        }
        for _ in 0..2 {
            kinds.push(5);
            parents.push(0);
            roots.push(0);
        }

        let n = kinds.len();
        let mut out = vec![0.0f32; n * 4];
        compute_layout_3d(&kinds, &parents, &roots, &[4, 5], &[5, 6], &[1, 1], &mut out);

        // Verify all coordinates are finite and pairwise separated.
        for i in 0..n {
            for k in 0..4 {
                assert!(out[i * 4 + k].is_finite());
            }
            for j in (i + 1)..n {
                let dx = out[i * 4] - out[j * 4];
                let dy = out[i * 4 + 1] - out[j * 4 + 1];
                let dz = out[i * 4 + 2] - out[j * 4 + 2];
                let d = (dx * dx + dy * dy + dz * dz).sqrt();
                assert!(d > 1.2, "3D nodes {i} and {j} collided (dist={d})");
            }
        }

        // Verify epistemic separation: hypotheses (kind 5) sit higher in Y than root paper.
        let paper_y = out[1];
        for i in 0..n {
            if kinds[i] == 5 {
                assert!(
                    out[i * 4 + 1] > paper_y + 3.0,
                    "Hypothesis {i} should sit above evidence plane"
                );
            }
        }
    }

    mod pbt {
        use super::*;
        use proptest::prelude::*;

        #[derive(Debug, Clone)]
        struct Layout2dCase {
            group_count: usize,
            group_ids: Vec<u32>,
            kind_ranks: Vec<u32>,
            widths: Vec<f32>,
            heights: Vec<f32>,
            edge_srcs: Vec<u32>,
            edge_dsts: Vec<u32>,
            edge_weights: Vec<f32>,
            max_cols: Vec<u32>,
            bary_flags: Vec<u32>,
            params: [f32; 8],
        }

        fn layout_2d_strategy() -> impl Strategy<Value = Layout2dCase> {
            (1usize..=8, 1usize..=48).prop_flat_map(|(group_count, node_count)| {
                let g_max = (group_count - 1) as u32;
                let n_max = (node_count - 1) as u32;
                (
                    prop::collection::vec(0u32..=g_max, node_count),
                    prop::collection::vec(0u32..=6, node_count),
                    prop::collection::vec(80.0f32..=420.0, node_count),
                    prop::collection::vec(24.0f32..=280.0, node_count),
                    prop::collection::vec((0u32..=n_max, 0u32..=n_max, 0.0f32..=5.0), 0..=40),
                    prop::collection::vec(0u32..=4, group_count),
                    prop::collection::vec(0u32..=1, group_count),
                    (
                        -10.0f32..=48.0,
                        -10.0f32..=84.0,
                        -10.0f32..=48.0,
                        -10.0f32..=48.0,
                        -10.0f32..=48.0,
                        -10.0f32..=96.0,
                        -10.0f32..=96.0,
                        -2.0f32..=6.0,
                    ),
                )
                    .prop_map(
                        move |(
                            group_ids,
                            kind_ranks,
                            widths,
                            heights,
                            edges,
                            max_cols,
                            bary_flags,
                            p,
                        )| {
                            let mut edge_srcs = Vec::with_capacity(edges.len());
                            let mut edge_dsts = Vec::with_capacity(edges.len());
                            let mut edge_weights = Vec::with_capacity(edges.len());
                            for (u, v, w) in edges {
                                edge_srcs.push(u);
                                edge_dsts.push(v);
                                edge_weights.push(w);
                            }
                            Layout2dCase {
                                group_count,
                                group_ids,
                                kind_ranks,
                                widths,
                                heights,
                                edge_srcs,
                                edge_dsts,
                                edge_weights,
                                max_cols,
                                bary_flags,
                                params: [p.0, p.1, p.2, p.3, p.4, p.5, p.6, p.7],
                            }
                        },
                    )
            })
        }

        proptest! {
            #![proptest_config(ProptestConfig::with_cases(128))]

            #[test]
            fn prop_layout_2d_zero_overlap_containment_and_determinism(case in layout_2d_strategy()) {
                let n = case.group_ids.len();
                let mut node_xy_a = vec![0.0f32; n * 2];
                let mut rects_a = vec![0.0f32; case.group_count * 4];
                let mut node_xy_b = vec![0.0f32; n * 2];
                let mut rects_b = vec![0.0f32; case.group_count * 4];

                compute_layout_2d(
                    &case.group_ids,
                    &case.kind_ranks,
                    &case.widths,
                    &case.heights,
                    &case.edge_srcs,
                    &case.edge_dsts,
                    &case.edge_weights,
                    case.group_count,
                    &case.max_cols,
                    &case.bary_flags,
                    &case.params,
                    &mut node_xy_a,
                    &mut rects_a,
                );
                compute_layout_2d(
                    &case.group_ids,
                    &case.kind_ranks,
                    &case.widths,
                    &case.heights,
                    &case.edge_srcs,
                    &case.edge_dsts,
                    &case.edge_weights,
                    case.group_count,
                    &case.max_cols,
                    &case.bary_flags,
                    &case.params,
                    &mut node_xy_b,
                    &mut rects_b,
                );

                // 1. Determinism and finiteness.
                prop_assert_eq!(&node_xy_a, &node_xy_b);
                prop_assert_eq!(&rects_a, &rects_b);
                prop_assert!(node_xy_a.iter().all(|v| v.is_finite()));
                prop_assert!(rects_a.iter().all(|v| v.is_finite()));

                // 2. Pairwise 0-overlap across ALL laid-out cards (effective clamped dimensions).
                for i in 0..n {
                    let xi = node_xy_a[i * 2];
                    let yi = node_xy_a[i * 2 + 1];
                    let wi = case.widths[i].max(160.0);
                    let hi = case.heights[i].max(44.0);
                    for j in (i + 1)..n {
                        let xj = node_xy_a[j * 2];
                        let yj = node_xy_a[j * 2 + 1];
                        let wj = case.widths[j].max(160.0);
                        let hj = case.heights[j].max(44.0);
                        let overlap_x = xi < xj + wj - 1e-3 && xi + wi > xj + 1e-3;
                        let overlap_y = yi < yj + hj - 1e-3 && yi + hi > yj + 1e-3;
                        prop_assert!(
                            !(overlap_x && overlap_y),
                            "2D card overlap between {i} ({xi},{yi},{wi},{hi}) and {j} ({xj},{yj},{wj},{hj})"
                        );
                    }
                }

                // 3. Pairwise 0-overlap across non-empty group lane rects.
                let non_empty_groups: Vec<usize> = (0..case.group_count)
                    .filter(|&g| rects_a[g * 4 + 2] > 0.0 && rects_a[g * 4 + 3] > 0.0)
                    .collect();
                for idx_a in 0..non_empty_groups.len() {
                    let ga = non_empty_groups[idx_a];
                    let (xa, ya, wa, ha) = (
                        rects_a[ga * 4],
                        rects_a[ga * 4 + 1],
                        rects_a[ga * 4 + 2],
                        rects_a[ga * 4 + 3],
                    );
                    for &gb in &non_empty_groups[(idx_a + 1)..] {
                        let (xb, yb, wb, hb) = (
                            rects_a[gb * 4],
                            rects_a[gb * 4 + 1],
                            rects_a[gb * 4 + 2],
                            rects_a[gb * 4 + 3],
                        );
                        let ox = xa < xb + wb - 1e-3 && xa + wa > xb + 1e-3;
                        let oy = ya < yb + hb - 1e-3 && ya + ha > yb + 1e-3;
                        prop_assert!(
                            !(ox && oy),
                            "Lane overlap between groups {ga} and {gb}"
                        );
                    }
                }

                // 4. Every card is strictly contained inside its owning lane bounding box.
                for i in 0..n {
                    let g = (case.group_ids[i] as usize).min(case.group_count - 1);
                    let (gx, gy, gw, gh) = (
                        rects_a[g * 4],
                        rects_a[g * 4 + 1],
                        rects_a[g * 4 + 2],
                        rects_a[g * 4 + 3],
                    );
                    let xi = node_xy_a[i * 2];
                    let yi = node_xy_a[i * 2 + 1];
                    let wi = case.widths[i].max(160.0);
                    let hi = case.heights[i].max(44.0);
                    prop_assert!(xi >= gx - 1e-2 && xi + wi <= gx + gw + 1e-2);
                    prop_assert!(yi >= gy - 1e-2 && yi + hi <= gy + gh + 1e-2);
                }
            }

            #[test]
            fn prop_layout_3d_finite_deterministic_and_epistemic_elevation(
                n_sections in 1usize..=6,
                n_equations in 1usize..=14,
                n_symbols in 0usize..=10,
                n_concepts in 1usize..=4,
                n_hypotheses in 1usize..=4,
            ) {
                let mut kinds = vec![0u32];
                let mut parents = vec![-1i32];
                let mut roots = vec![0i32];

                for _ in 0..n_sections {
                    kinds.push(1);
                    parents.push(0);
                    roots.push(0);
                }
                for eq in 0..n_equations {
                    kinds.push(2);
                    parents.push(1 + (eq % n_sections) as i32);
                    roots.push(0);
                }
                let first_eq = 1 + n_sections;
                for sym in 0..n_symbols {
                    kinds.push(3);
                    parents.push((first_eq + (sym % n_equations)) as i32);
                    roots.push(0);
                }
                for _ in 0..n_concepts {
                    kinds.push(4);
                    parents.push(0);
                    roots.push(0);
                }
                for _ in 0..n_hypotheses {
                    kinds.push(5);
                    parents.push(0);
                    roots.push(0);
                }

                let n = kinds.len();
                let mut edge_srcs = Vec::new();
                let mut edge_dsts = Vec::new();
                let mut edge_codes = Vec::new();
                for eq in 0..n_equations.saturating_sub(1) {
                    edge_srcs.push((first_eq + eq) as u32);
                    edge_dsts.push((first_eq + eq + 1) as u32);
                    edge_codes.push(1u32);
                }

                let mut out_a = vec![0.0f32; n * 4];
                let mut out_b = vec![0.0f32; n * 4];
                compute_layout_3d(&kinds, &parents, &roots, &edge_srcs, &edge_dsts, &edge_codes, &mut out_a);
                compute_layout_3d(&kinds, &parents, &roots, &edge_srcs, &edge_dsts, &edge_codes, &mut out_b);

                prop_assert_eq!(&out_a, &out_b);
                for i in 0..n {
                    for k in 0..4 {
                        prop_assert!(out_a[i * 4 + k].is_finite());
                    }
                    let progress = out_a[i * 4 + 3];
                    prop_assert!((0.02..=1.0).contains(&progress));
                }

                // Epistemic stratification invariant: hypotheses float above root paper, concepts below.
                let paper_y = out_a[1];
                for i in 0..n {
                    if kinds[i] == 5 {
                        prop_assert!(
                            out_a[i * 4 + 1] > paper_y + 1.0,
                            "Hypothesis node {i} dropped below epistemic halo"
                        );
                    } else if kinds[i] == 4 {
                        prop_assert!(
                            out_a[i * 4 + 1] < paper_y - 1.0,
                            "Concept node {i} drifted above semantic support ring"
                        );
                    }
                }
            }

            #[test]
            fn prop_layout_3d_arbitrary_inputs_never_panic_and_stay_finite(
                node_count in 1usize..=40,
                seed_kinds in prop::collection::vec(0u32..=9, 40),
                seed_parents in prop::collection::vec(-5i32..=50, 40),
                seed_roots in prop::collection::vec(-3i32..=10, 40),
                edges in prop::collection::vec((0u32..=60, 0u32..=60, 0u32..=6), 0..=40),
            ) {
                let kinds = &seed_kinds[..node_count];
                let parents = &seed_parents[..node_count];
                let roots = &seed_roots[..node_count];
                let mut edge_srcs = Vec::with_capacity(edges.len());
                let mut edge_dsts = Vec::with_capacity(edges.len());
                let mut edge_codes = Vec::with_capacity(edges.len());
                for (u, v, c) in edges {
                    edge_srcs.push(u);
                    edge_dsts.push(v);
                    edge_codes.push(c);
                }

                let mut out_a = vec![0.0f32; node_count * 4];
                let mut out_b = vec![0.0f32; node_count * 4];
                compute_layout_3d(kinds, parents, roots, &edge_srcs, &edge_dsts, &edge_codes, &mut out_a);
                compute_layout_3d(kinds, parents, roots, &edge_srcs, &edge_dsts, &edge_codes, &mut out_b);

                prop_assert_eq!(&out_a, &out_b);
                prop_assert!(out_a.iter().all(|v| v.is_finite()));
                for i in 0..node_count {
                    prop_assert!((0.02..=1.0).contains(&out_a[i * 4 + 3]));
                }
            }
        }
    }
}
