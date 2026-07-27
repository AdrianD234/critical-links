/*
 * Composition glue.
 *
 * Deliberately thin. The previous App.tsx held the search box, the controls,
 * the URL reader, the fetch logic and the result panel in one component, which
 * meant every one of those concerns had to be reasoned about together. Here it
 * installs the query client and renders the active mode; nothing else.
 */

import { QueryClientProvider } from '@tanstack/react-query';

import ExploreScreen from './ExploreScreen.js';
import { createQueryClient } from './state/queries.js';

const queryClient = createQueryClient();

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ExploreScreen />
    </QueryClientProvider>
  );
}
