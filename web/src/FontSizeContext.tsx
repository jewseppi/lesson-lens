import { createContext, useContext, useState, type ReactNode } from 'react';

type FontSize = 'normal' | 'large' | 'xl';

interface FontSizeContextType {
  fontSize: FontSize;
  cycle: () => void;
  zhClass: string;
}

const SIZES: FontSize[] = ['normal', 'large', 'xl'];
const ZH_CLASSES: Record<FontSize, string> = {
  normal: 'zh-text zh-normal',
  large: 'zh-text zh-large',
  xl: 'zh-text zh-xl',
};
const LABELS: Record<FontSize, string> = {
  normal: 'A',
  large: 'A+',
  xl: 'A++',
};

const FontSizeContext = createContext<FontSizeContextType | null>(null);

function getInitial(): FontSize {
  const stored = localStorage.getItem('zh-font-size');
  if (stored && SIZES.includes(stored as FontSize)) return stored as FontSize;
  return 'large';
}

export function FontSizeProvider({ children }: { children: ReactNode }) {
  const [fontSize, setFontSize] = useState<FontSize>(getInitial);

  const cycle = () => {
    const next = SIZES[(SIZES.indexOf(fontSize) + 1) % SIZES.length];
    setFontSize(next);
    localStorage.setItem('zh-font-size', next);
  };

  return (
    <FontSizeContext.Provider value={{ fontSize, cycle, zhClass: ZH_CLASSES[fontSize] }}>
      {children}
    </FontSizeContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useFontSize() {
  const ctx = useContext(FontSizeContext);
  if (!ctx) throw new Error('useFontSize must be inside FontSizeProvider');
  return ctx;
}

export function FontSizeToggle() {
  const { fontSize, cycle } = useFontSize();
  return (
    <button
      onClick={cycle}
      title={`Chinese text size: ${fontSize}`}
      className="text-sm text-gray-400 hover:text-white transition-colors bg-gray-800 px-2 py-1 rounded"
    >
      字 {LABELS[fontSize]}
    </button>
  );
}
