/*
 * The sticky footer: the standing caveat and the two actions.
 *
 * The caveat is in the footer rather than a disclosure because it qualifies
 * every figure above it, and it stays on screen while the panel scrolls.
 *
 * Copy failure is never a silent no-op — if the clipboard is unavailable or
 * refused, the URL is revealed in a selectable field with a short explanation.
 */

import { useEffect, useRef, useState } from 'react';

import { CheckIcon, DownloadIcon, ShareIcon } from '../shell/icons.js';

export default function InspectorActions({
  permalink,
  onExport,
  canExport,
}: {
  permalink: string | null;
  onExport: () => void;
  canExport: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const [fallback, setFallback] = useState<string | null>(null);
  const fieldRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!copied) return;
    const t = window.setTimeout(() => setCopied(false), 1600);
    return () => window.clearTimeout(t);
  }, [copied]);

  useEffect(() => {
    if (fallback && fieldRef.current) {
      fieldRef.current.focus();
      fieldRef.current.select();
    }
  }, [fallback]);

  async function copy() {
    if (!permalink) return;
    try {
      await navigator.clipboard.writeText(permalink);
      setCopied(true);
      setFallback(null);
    } catch {
      setFallback(permalink);
    }
  }

  return (
    <>
      <div className="caveat">
        Structural replacement path — not a traffic forecast.
      </div>

      {fallback && (
        <div style={{ display: 'grid', gap: 5 }}>
          <label
            htmlFor="permalink-fallback"
            style={{ fontSize: 'var(--text-meta)', color: 'var(--panel-muted)' }}
          >
            Clipboard unavailable — copy this link manually:
          </label>
          <input
            id="permalink-fallback"
            ref={fieldRef}
            readOnly
            value={fallback}
            style={{
              width: '100%',
              height: 'var(--control-h)',
              padding: '0 8px',
              border: 'var(--rule)',
              borderRadius: 'var(--radius)',
              background: 'var(--panel-inset)',
              fontFamily: 'var(--font-mono)',
              fontSize: 'var(--text-meta)',
              color: 'var(--panel-fg)',
            }}
          />
        </div>
      )}

      <div className="actions">
        <button
          type="button"
          className="pbtn"
          onClick={copy}
          disabled={!permalink}
        >
          {copied ? <CheckIcon size={12} /> : <ShareIcon size={12} />}
          {copied ? 'Copied' : 'Copy link'}
        </button>
        <button
          type="button"
          className="pbtn"
          onClick={onExport}
          disabled={!canExport}
        >
          <DownloadIcon size={12} />
          Download
        </button>
      </div>
    </>
  );
}
