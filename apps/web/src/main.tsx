import React from 'react';
import { createRoot } from 'react-dom/client';

/*
 * Fonts are imported here so Vite fingerprints and bundles the woff2 files into
 * the build output. Both faces are SIL OFL 1.1, which permits self-hosting, and
 * nothing is fetched from a font CDN: the application must render identically
 * offline and must not leak a request to a third party on every page load.
 *
 * Only the weights the design actually uses are imported. Inter is a variable
 * font, so one file covers the 400–680 range the tokens call for; JetBrains
 * Mono is used at two weights for identifiers and comparison figures.
 */
import '@fontsource-variable/inter';
import '@fontsource/jetbrains-mono/400.css';
import '@fontsource/jetbrains-mono/600.css';

import 'maplibre-gl/dist/maplibre-gl.css';

import './styles/tokens.css';
import './styles/base.css';
import './styles/shell.css';
import './styles/inspector.css';
import './styles/map.css';
/* The editor is behind a flag, but its stylesheet is not: a few hundred bytes
 * of unused CSS costs less than a conditional import that could load after the
 * panel it styles. */
import './styles/span.css';
/* Last, so its breakpoint overrides win without needing specificity tricks. */
import './styles/responsive.css';

import App from './App.js';

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
