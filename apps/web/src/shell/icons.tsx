/*
 * Inline icons.
 *
 * Bundled as components rather than an icon font or a sprite sheet so nothing
 * is fetched at runtime and the strokes inherit `currentColor` — the rail, the
 * top bar and the paper inspector all need the same glyph in different inks.
 *
 * Every icon is decorative by default (`aria-hidden`). Icon-only controls carry
 * their accessible name on the button, not on the glyph.
 */

interface IconProps {
  size?: number;
  className?: string;
}

function svg(size: number, className: string | undefined, path: React.ReactNode) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
      focusable="false"
    >
      {path}
    </svg>
  );
}

export const SearchIcon = ({ size = 14, className }: IconProps) =>
  svg(size, className, (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </>
  ));

export const LayersIcon = ({ size = 17, className }: IconProps) =>
  svg(size, className, (
    <>
      <path d="M12 3 3 8l9 5 9-5-9-5Z" />
      <path d="m3 13 9 5 9-5" />
    </>
  ));

export const BasemapIcon = ({ size = 17, className }: IconProps) =>
  svg(size, className, (
    <>
      <path d="m3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3V6Z" />
      <path d="M9 3v15M15 6v15" />
    </>
  ));

export const InfoIcon = ({ size = 17, className }: IconProps) =>
  svg(size, className, (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v5M12 8h.01" />
    </>
  ));

export const FlagIcon = ({ size = 17, className }: IconProps) =>
  svg(size, className, (
    <>
      <path d="M12 3v18" />
      <path d="M12 4.5 19 8l-7 3.5" />
    </>
  ));

export const WarningIcon = ({ size = 17, className }: IconProps) =>
  svg(size, className, (
    <>
      <path d="M12 4.5 21 20H3l9-15.5Z" />
      <path d="M12 10v4M12 17h.01" />
    </>
  ));

export const ShareIcon = ({ size = 13, className }: IconProps) =>
  svg(size, className, (
    <>
      <path d="M4 12v7a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-7" />
      <path d="M12 15V3M8 7l4-4 4 4" />
    </>
  ));

export const DownloadIcon = ({ size = 13, className }: IconProps) =>
  svg(size, className, (
    <>
      <path d="M4 13v6a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-6" />
      <path d="M12 3v12M8 11l4 4 4-4" />
    </>
  ));

export const CheckIcon = ({ size = 13, className }: IconProps) =>
  svg(size, className, <path d="m5 12.5 4.5 4.5L19 7" />);

export const CloseIcon = ({ size = 13, className }: IconProps) =>
  svg(size, className, <path d="m6 6 12 12M18 6 6 18" />);

export const ChevronIcon = ({ size = 13, className }: IconProps) =>
  svg(size, className, <path d="m8 5 7 7-7 7" />);

/**
 * The brand mark. A closed link with the network routing around it, which is
 * the entire product in one glyph.
 */
export function BrandMark({ size = 22 }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      className="brand-mark"
      aria-hidden="true"
      focusable="false"
    >
      <rect width="24" height="24" rx="5" fill="#222a31" />
      <path
        d="M5 18C7.5 18 8 6.5 12.5 6.5S18 13 19 13"
        fill="none"
        stroke="#2de1c2"
        strokeWidth="2"
        strokeLinecap="round"
      />
      <path
        d="m9 15 6-6"
        stroke="#ff4d4d"
        strokeWidth="2.4"
        strokeLinecap="round"
      />
    </svg>
  );
}
