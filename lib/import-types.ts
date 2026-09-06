export type ImportedEquation = {
  equation_id: string;
  anchor: string;
  anchor_is_source: boolean;
  latex: string;
  source_fragments: string[];
  warnings: string[];
  equation_number: string | null;
  section: string | null;
  section_id: string | null;
  preceding_text: string | null;
  following_text: string | null;
  extraction_method:
    | 'tex_annotation'
    | 'alttext'
    | 'mathml_text'
    | 'assembled_tex';
  confidence: number;
};

export type ImportedSection = {
  section_id: string;
  anchor: string;
  anchor_is_source: boolean;
  title: string;
  order: number;
  parent_section_id: string | null;
  text: string;
  equation_ids: string[];
};

export type ImportedPaper = {
  paper_id: string;
  version: number | null;
  title: string;
  authors: string[];
  version_published_at: string | null;
  metadata_warnings: string[];
  source_url: string;
  source_sha256: string;
  sections: ImportedSection[];
  equations: ImportedEquation[];
};

export type EvidenceImportReceipt = {
  import_uuid: string;
  node_count: number;
  edge_count: number;
  episode_count: number;
  replayed: boolean;
};

export type WorkspaceImportResponse = {
  job_id: string;
  paper: ImportedPaper;
  receipt: EvidenceImportReceipt;
};

export type EvidenceEntityType =
  | 'Paper'
  | 'PaperVersion'
  | 'Section'
  | 'Equation'
  | 'Symbol'
  | 'Assumption'
  | 'Claim'
  | 'Concept'
  | 'Method'
  | 'Experiment'
  | 'Hypothesis';

export type VerificationStatus =
  | 'reported'
  | 'draft'
  | 'invalid'
  | 'well_typed'
  | 'numerically_plausible'
  | 'symbolically_verified'
  | 'human_reviewed';

export type EvidenceSearchInput = {
  query: string;
  paper_id?: string;
  version?: number;
  entity_types?: EvidenceEntityType[];
  verification_statuses?: VerificationStatus[];
  as_of?: string;
  center_node_uuid?: string;
  limit?: number;
  cursor?: string;
};

export type EvidenceSearchHit = {
  uuid: string;
  kind: EvidenceEntityType;
  logical_id: string;
  paper_id: string;
  version: number | null;
  valid_at: string | null;
  verification_status: VerificationStatus;
  payload: Record<string, unknown>;
  episode_uuids: string[];
  score: number;
  match_sources: Array<'lexical' | 'semantic' | 'graph'>;
  score_components: {
    lexical_rank: number | null;
    semantic_rank: number | null;
    graph_rank: number | null;
    graph_distance: number | null;
  };
};

export type EvidenceSearchResponse = {
  hits: EvidenceSearchHit[];
  next_cursor: string | null;
  semantic_available: boolean;
};
