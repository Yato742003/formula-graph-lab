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
