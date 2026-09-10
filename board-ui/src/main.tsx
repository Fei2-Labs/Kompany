import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
// HashRouter (not BrowserRouter): the board is served at `/` by the same FastAPI
// app that owns dozens of top-level API paths (/projects, /agents, /inbox, ...).
// Path-based client routes collide with those on reload/deep-link; hash routes
// (/#/projects) never reach the server, so collisions are impossible.
import { HashRouter } from 'react-router-dom';
import { App } from './App';
import './index.css';
import './studio/skins.css';
import { bootSkin } from './studio/useSkin';

bootSkin();

const container = document.getElementById('root');
if (!container) {
  throw new Error('root element not found');
}

createRoot(container).render(
  <StrictMode>
    <HashRouter>
      <App />
    </HashRouter>
  </StrictMode>,
);
