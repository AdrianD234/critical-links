/*
 * Share and Export.
 *
 * Both are disabled until there is a result to share or export, because a
 * permalink to "no selection" and an export of nothing are not useful actions
 * and offering them implies the app has state it does not have.
 *
 * Copy failure is never a silent no-op: if the clipboard API is unavailable or
 * refused, the caller falls back to revealing the URL for manual selection.
 */

import { useEffect, useState } from 'react';

import { CheckIcon, DownloadIcon, ShareIcon } from './icons.js';

export default function ShareExportActions({
  permalink,
  canExport,
  onExport,
  onCopyFailed,
}: {
  permalink: string | null;
  canExport: boolean;
  onExport: () => void;
  onCopyFailed: (url: string) => void;
}) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const t = window.setTimeout(() => setCopied(false), 1600);
    return () => window.clearTimeout(t);
  }, [copied]);

  async function share() {
    if (!permalink) return;
    const url = new URL(permalink, window.location.origin).toString();
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
    } catch {
      onCopyFailed(url);
    }
  }

  return (
    <>
      <button
        type="button"
        className="btn"
        onClick={share}
        disabled={!permalink}
        title={
          permalink
            ? 'Copy a link to this exact result'
            : 'Select a road first'
        }
      >
        {copied ? <CheckIcon /> : <ShareIcon />}
        {copied ? 'Copied' : 'Share'}
      </button>
      <button
        type="button"
        className="btn"
        onClick={onExport}
        disabled={!canExport}
        title={canExport ? 'Download this result as GeoJSON' : 'Select a road first'}
      >
        <DownloadIcon />
        Export
      </button>
    </>
  );
}
