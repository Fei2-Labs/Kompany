// Phone app shell (<=720px): top app bar, bottom tab bar, and a drawer for the
// remaining panes. Desktop hides all three via CSS and keeps the sidebar rail.

import { useEffect, useState } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { MOBILE_TABS } from '../nav';
import { NavList } from './Sidebar';

export function MobileBar({ onOpenPalette, onOpenMenu }: { onOpenPalette: () => void; onOpenMenu: () => void }) {
  return (
    <header className="mbar">
      <span className="mbar__brand">
        <span className="workspace__avatar">K</span>
        <span className="mbar__name">Kompany</span>
      </span>
      <button type="button" className="mbar__btn" aria-label="Search" onClick={onOpenPalette}>
        ⌕
      </button>
      <button type="button" className="mbar__btn" aria-label="Menu" onClick={onOpenMenu}>
        ☰
      </button>
    </header>
  );
}

export function MobileTabs({ onOpenMenu }: { onOpenMenu: () => void }) {
  return (
    <nav className="mtabs" aria-label="Primary">
      {MOBILE_TABS.map((tab) => (
        <NavLink
          key={tab.label}
          to={tab.path ?? '/'}
          end={tab.path === '/'}
          className={({ isActive }) => (isActive ? 'mtabs__tab mtabs__tab--active' : 'mtabs__tab')}
        >
          <span className="mtabs__glyph">{tab.glyph}</span>
          <span className="mtabs__label">{tab.label}</span>
        </NavLink>
      ))}
      <button type="button" className="mtabs__tab" onClick={onOpenMenu}>
        <span className="mtabs__glyph">⋯</span>
        <span className="mtabs__label">More</span>
      </button>
    </nav>
  );
}

export function MobileDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const location = useLocation();
  const [seen, setSeen] = useState(location.pathname);
  // Close on any route change (back button, in-app links).
  useEffect(() => {
    if (location.pathname !== seen) {
      setSeen(location.pathname);
      if (open) onClose();
    }
  }, [location.pathname, seen, open, onClose]);

  if (!open) return null;
  return (
    <div className="mdrawer" role="dialog" aria-modal="true" aria-label="Navigation">
      <div className="mdrawer__scrim" onClick={onClose} />
      <div className="mdrawer__panel">
        <button type="button" className="mdrawer__close" aria-label="Close menu" onClick={onClose}>
          ✕
        </button>
        <NavList onNavigate={onClose} />
      </div>
    </div>
  );
}
