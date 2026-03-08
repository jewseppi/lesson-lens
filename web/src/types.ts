export interface User {
  email: string;
  display_name: string;
  is_admin: boolean;
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
