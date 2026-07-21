import { NavLink } from 'react-router-dom';
import { NAV_GROUPS, TOP_ITEMS, type NavItem } from '../nav';

interface SidebarProps {
  onOpenPalette: () => void;
}

function ItemRow({ item }: { item: NavItem }) {
  if (item.disabled) {
    return (
      <div className="nav__item nav__item--disabled">
        <span className="nav__glyph">{item.glyph}</span>
        <span className="nav__label">{item.label}</span>
        <span className="nav__soon">{item.disabledTag ?? 'soon'}</span>
      </div>
    );
  }

  if (item.href) {
    return (
      <a className="nav__item" href={item.href}>
        <span className="nav__glyph">{item.glyph}</span>
        <span className="nav__label">{item.label}</span>
      </a>
    );
  }

  return (
    <NavLink
      to={item.path ?? '/'}
      end={item.path === '/'}
      className={({ isActive }) =>
        isActive ? 'nav__item nav__item--active' : 'nav__item'
      }
    >
      <span className="nav__glyph">{item.glyph}</span>
      <span className="nav__label">{item.label}</span>
    </NavLink>
  );
}

export function Sidebar({ onOpenPalette }: SidebarProps) {
  return (
    <aside className="sidebar">
      <button className="workspace" type="button">
        <span className="workspace__avatar">K</span>
        <span className="workspace__name">Kompany</span>
        <span className="workspace__caret">▾</span>
      </button>

      <button className="search" type="button" onClick={onOpenPalette}>
        <span className="search__glyph">⌕</span>
        <span className="search__label">Search</span>
        <span className="search__kbd">⌘K</span>
      </button>

      <nav className="nav">
        <div className="nav__group">
          {TOP_ITEMS.map((item) => (
            <ItemRow key={item.label} item={item} />
          ))}
        </div>

        {NAV_GROUPS.map((group) => (
          <div className="nav__group" key={group.label}>
            <div className="nav__group-label">{group.label}</div>
            {group.items.map((item) => (
              <ItemRow key={item.label} item={item} />
            ))}
          </div>
        ))}
      </nav>
    </aside>
  );
}
