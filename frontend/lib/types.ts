// Types miroir des Pydantic models du backend (backend/models.py).
// À maintenir en cohérence manuelle jusqu'à ce qu'on ajoute une génération OpenAPI.

export type GenerationMode = "brief" | "full";

/**
 * Étanchéité entre cocons. `strict` par défaut : la méthode veut qu'une page
 * d'un cocon ne lie pas vers un autre cocon, les cocons étant déjà reliés par
 * le haut de l'arbre.
 */
export type InterCoconPolicy = "strict" | "mothers_only" | "libre";

export type SearchIntent =
  | "informational"
  | "commercial"
  | "transactional"
  | "navigational";

export type ArticleType = "mother" | "daughter";

export type InternalLinkType =
  | "daughter_to_mother"
  | "mother_to_daughter"
  | "sister_to_sister"
  | "cross_cocon";

/**
 * `awaiting_validation` n'est pas un statut RQ : le backend le synthétise quand
 * un job s'est terminé sur une pause de validation plutôt que sur un résultat.
 */
export type JobStatus =
  | "queued"
  | "started"
  | "finished"
  | "failed"
  | "deferred"
  | "awaiting_validation";

export type PipelineStep =
  | "keyword_research"
  | "awaiting_validation"
  | "cocon_design"
  | "serp_analysis"
  | "article_generation"
  | "maillage"
  | "backlinks"
  | "complete";

export interface ExperienceElement {
  type: "case_study" | "data" | "screenshot" | "insight" | "quote";
  title: string;
  content: string;
  source?: string | null;
}

/**
 * Article existant du client, injecté en few-shot pour caler la voix.
 * Optionnel, mais c'est le levier le plus fort dont on dispose sur le rendu.
 */
export interface StyleSample {
  title?: string | null;
  content: string;
  source?: string | null;
}

export interface ClientForm {
  product: string;
  description: string;
  language: string;
  seed_keywords: string[];
  audience: string;
  niche: string;
  num_cocoons: number;
  mode: GenerationMode;
  /**
   * Suspend le run après la recherche de mots-clés, le temps que l'agence
   * valide la sélection. Omis = `true` côté backend.
   */
  validate_keywords?: boolean;
  /** Omis = `strict` côté backend. */
  inter_cocon_policy?: InterCoconPolicy;
  experience_elements: ExperienceElement[];
  /** Max 5 côté backend. */
  style_samples?: StyleSample[];
  agency_id?: string | null;
  client_project_name?: string | null;
}

export interface ArticleStub {
  cocon_id: string;
  article_type: ArticleType;
  target_keyword: string;
  secondary_keywords: string[];
  h1_title: string;
  meta_title: string;
  meta_description: string;
  slug: string;
  intent: SearchIntent;
}

export interface CoconStructure {
  id: string;
  theme: string;
  main_keyword: string;
  mother: ArticleStub;
  daughters: ArticleStub[];
  rationale: string;
}

export interface KeywordWithData {
  keyword: string;
  intent: SearchIntent;
  cluster: string;
  monthly_volume: number | null;
  cpc: number | null;
  competition_score: number | null;
  difficulty: number | null;
}

export interface RunUsage {
  claude_calls: number;
  input_tokens: number;
  output_tokens: number;
  cache_creation_tokens: number;
  cache_read_tokens: number;
  claude_cost_usd: number;
  claude_cost_by_tier: Record<string, number>;
  cache_savings_usd: number;
  dataforseo_cost_usd: number;
}

export interface SerpAnalysis {
  keyword: string;
  scraped_pages_count: number;
  serp_urls_count: number;
  rejected_pages: Record<string, number>;
  low_sample: boolean;
  avg_word_count: number;
  recommended_word_count: number;
  avg_h2_count: number;
  recommended_h2_count: number;
  avg_h3_count: number;
  key_entities: string[];
  key_topics: string[];
  common_questions: string[];
  content_gaps: string[];
  competitive_angle: string;
  top_result_format: string;
}

export interface InternalLink {
  anchor_text: string;
  target_slug: string;
  target_h1: string;
  link_type: InternalLinkType;
  context: string;
  justification: string;
}

export interface ExternalLink {
  anchor_text: string;
  url_suggestion: string | null;
  domain_type: string;
  reason: string;
}

export interface ArticleSection {
  h2: string;
  h3s: string[];
  key_points: string[];
  word_count_target: number;
}

export interface FAQItem {
  question: string;
  answer: string;
}

export interface EEATScore {
  experience: number;
  expertise: number;
  authoritativeness: number;
  trustworthiness: number;
  overall: number;
  warnings: string[];
}

export interface GeneratedArticle {
  stub: ArticleStub;
  serp_analysis: SerpAnalysis;
  sections: ArticleSection[];
  faq: FAQItem[];
  internal_links: InternalLink[];
  external_links: ExternalLink[];
  eeat_score: EEATScore | null;
  schema_jsonld: Record<string, unknown>;
  content_markdown: string;
  word_count: number;
}

export interface ArticleBrief {
  stub: ArticleStub;
  serp_analysis: SerpAnalysis;
  sections: ArticleSection[];
  faq_questions: string[];
  internal_links_plan: InternalLink[];
  external_links_suggestions: ExternalLink[];
  editorial_notes: string;
  tone_guidance: string;
  unique_angle: string;
}

export interface InterCoconLink {
  from_cocon_id: string;
  from_slug: string;
  to_cocon_id: string;
  to_slug: string;
  anchor_text: string;
  justification: string;
}

export interface MaillageMap {
  links: Record<string, InternalLink[]>;
  inter_cocon_links: InterCoconLink[];
}

export interface AnchorRatio {
  exact: number;
  partial: number;
  branded: number;
  naked_url: number;
  generic: number;
}

export interface BacklinkOpportunity {
  referring_domain: string;
  domain_rating: number | null;
  reason: string;
  suggested_anchor: string;
  contact_email: string | null;
  outreach_template_type: "guest_post" | "niche_edit" | "resource_page";
}

export interface CompetitorBacklinkSummary {
  competitor_url: string;
  total_backlinks: number;
  referring_domains: number;
  domain_rating: number | null;
  top_referring_types: string[];
}

export interface BacklinkReport {
  cocon_id: string;
  competitor_analysis: CompetitorBacklinkSummary[];
  opportunities: BacklinkOpportunity[];
  recommended_anchor_ratio: AnchorRatio;
  outreach_templates: Record<string, string>;
}

export interface PipelineResult {
  form: ClientForm;
  keywords_researched: KeywordWithData[];
  cocoons: CoconStructure[];
  briefs: ArticleBrief[];
  articles: GeneratedArticle[];
  maillage_map: MaillageMap;
  backlink_reports: BacklinkReport[];
  usage?: RunUsage | null;
  generated_at: string;
}

export interface JobProgress {
  step: PipelineStep;
  percent: number;
  message: string;
  current_item?: string | null;
}

export interface JobStatusResponse {
  job_id: string;
  /** Stable d'un job à l'autre : c'est lui qui adresse la validation. */
  run_id: string | null;
  status: JobStatus;
  progress: JobProgress | null;
  created_at: string | null;
  started_at: string | null;
  ended_at: string | null;
  result?: PipelineResult;
  error?: string;
  error_traceback?: string | null;
}

// ============================================================
// Validation humaine de la sélection de mots-clés
// ============================================================

/** Un mot-clé retenu par Claude, avec la raison qu'il en donne. */
export interface KeywordPick {
  keyword: string;
  role: ArticleType;
  reason: string;
  monthly_volume: number | null;
  cpc: number | null;
  competition_score: number | null;
  difficulty: number | null;
  intent: SearchIntent;
}

export interface CoconProposal {
  index: number;
  theme: string;
  main_keyword: string;
  rationale: string;
  picks: KeywordPick[];
}

export interface ValidationSnapshot {
  run_id: string;
  proposals: CoconProposal[];
  /** Tous les mots-clés enrichis du run, propositions comprises. */
  pool: KeywordWithData[];
  max_per_cocon: number;
  min_per_cocon: number;
}

export interface ValidatedCocon {
  index: number;
  theme?: string | null;
  mother_keyword: string;
  daughter_keywords: string[];
}

export interface ValidationDecision {
  cocoons: ValidatedCocon[];
}

/** Ligne d'historique renvoyée par GET /runs (sans le `result`, trop lourd). */
export interface RunSummary {
  id: string;
  job_id: string | null;
  agency_id: string | null;
  project_name: string | null;
  mode: GenerationMode;
  language: string;
  status: "queued" | "running" | "completed" | "failed";
  error: string | null;
  cocoons_count: number;
  articles_count: number;
  created_at: string;
  ended_at: string | null;
}
