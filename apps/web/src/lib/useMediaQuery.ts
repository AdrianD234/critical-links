/**
 * Subscribe to a media query.
 *
 * Layout decisions that change component *structure* — the inspector becoming
 * a bottom sheet, the resize grip disappearing — cannot be made in CSS alone,
 * because they change what is rendered and what event handlers exist. Those
 * read from here; anything that is purely visual stays in the stylesheet.
 *
 * The breakpoints must match the literals in the stylesheets. They are named
 * here so the coupling is at least visible.
 */

import { useEffect, useState } from 'react';

export const BP_MOBILE = 640;
export const BP_LAPTOP = 1100;

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() =>
    typeof window === 'undefined' ? false : window.matchMedia(query).matches,
  );

  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setMatches(mq.matches);
    on();
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, [query]);

  return matches;
}

export const useIsMobile = () => useMediaQuery(`(max-width: ${BP_MOBILE - 1}px)`);
export const useIsLaptop = () => useMediaQuery(`(max-width: ${BP_LAPTOP - 1}px)`);
