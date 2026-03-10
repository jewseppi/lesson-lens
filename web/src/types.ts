export interface User {
  email: string;
  display_name: string;
  is_admin: boolean;
  status?: string;
}

export interface SignupRequest {
  id: number;
  email: string;
  display_name: string;
  reason: string;
  status: 'pending' | 'approved' | 'denied';
  reviewed_by: number | null;
  reviewed_at: string | null;
  created_at: string;
}

export interface AdminUser {
  id: number;
  email: string;
  display_name: string;
  is_admin: boolean;
  status: string;
  last_login_at: string | null;
  created_at: string;
}

export interface Upload {
  id: number;
  original_filename: string;
  file_size: number;
  line_count: number;
  uploaded_at: string;
}

export interface ParseResult {
  run_id: string;
  session_count: number;
  message_count: number;
  lesson_content_count: number;
  warnings: number;
  duplicate?: boolean;
}

export interface Session {
  id: number;
  session_id: string;
  date: string;
  start_time: string;
  end_time: string;
  message_count: number;
  lesson_content_count: number;
  boundary_confidence: 'high' | 'medium' | 'low';
  topics: string[];
  has_summary: boolean;
  shared_links: SharedLink[];
}

export interface SharedLink {
  url: string;
  label?: string | null;
  speaker_role?: 'teacher' | 'student' | 'unknown';
  speaker_raw?: string;
  time?: string;
  before_text?: string | null;
  after_text?: string | null;
}

export interface Message {
  message_id: string;
  line_start: number;
  line_end: number;
  time: string;
  speaker_role: 'teacher' | 'student' | 'unknown';
  speaker_raw: string;
  message_type: 'lesson-content' | 'logistics' | 'media-reference' | 'call-system' | 'link' | 'other';
  text_raw: string;
  text_normalized: string;
  language_hint: string;
  tags: string[];
}

export interface SessionDetail {
  session_id: string;
  date: string;
  start_time: string;
  end_time: string;
  message_count: number;
  lesson_content_count: number;
  boundary_confidence: string;
  messages: Message[];
  shared_links: SharedLink[];
}

export interface KeySentence {
  id: string;
  zh: string;
  pinyin: string;
  zhuyin?: string;
  en: string;
  source_refs: string[];
  context_note?: string;
  pronunciation_note?: string;
}

export interface VocabItem {
  term_zh: string;
  pinyin: string;
  zhuyin?: string;
  en: string;
  pos_or_type: string;
  example_zh: string;
  example_en: string;
  example_pinyin?: string;
  example_zhuyin?: string;
  pronunciation_note?: string;
}

export interface Correction {
  id: string;
  learner_original: string;
  teacher_correction: string;
  reason: string;
  source_refs: string[];
}

export interface Flashcard {
  id: string;
  front: string;
  back: string;
  hint?: string;
}

export interface QuizQuestion {
  id: string;
  question: string;
  options: string[];
  correct_index: number;
  explanation?: string;
}

export interface LessonSummary {
  schema_version: string;
  lesson_id: string;
  lesson_date: string;
  title: string;
  summary: {
    overview: string;
    usage_notes: string;
    short_recap: string;
  };
  key_sentences: KeySentence[];
  vocabulary: VocabItem[];
  corrections: Correction[];
  review: {
    flashcards: Flashcard[];
    fill_blank: Array<{ id: string; sentence: string; answer: string; hint?: string }>;
    translation_drills: Array<{ id: string; source_lang: string; source_text: string; target_text: string }>;
    quiz: QuizQuestion[];
  };
}

export interface AttachmentUploadResult {
  filename: string;
  attachment_id?: number;
  status?: 'created' | 'duplicate';
  error?: string;
  timestamp_source?: string | null;
  captured_at_local?: string | null;
  match?: {
    session_id: string | null;
    confidence: 'high' | 'medium' | 'low' | 'unmatched';
    reason: string;
  };
}

export interface SessionAttachment {
  session_attachment_id: number;
  attachment_id: number;
  original_filename: string;
  mime_type: string;
  captured_at_local: string | null;
  match_confidence: 'high' | 'medium' | 'low' | 'unmatched';
  match_reason: string;
  assigned_by: 'auto' | 'manual';
}
