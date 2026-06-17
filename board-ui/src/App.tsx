import { useCallback, useEffect, useState } from 'react';
import { Route, Routes } from 'react-router-dom';
import { Sidebar } from './components/Sidebar';
import { CommandPalette } from './components/CommandPalette';
import { Placeholder } from './panes/Placeholder';
import { Board } from './panes/Board';
import { Agents } from './panes/Agents';
import { Usage } from './panes/Usage';
import { Projects } from './panes/Projects';
import { Autopilot } from './panes/Autopilot';
import { Runtimes } from './panes/Runtimes';
import { Live } from './panes/Live';
import { TalkToCeo } from './channel/TalkToCeo';
import { CommandBar } from './channel/CommandBar';
import { ActivityTimeline } from './timeline/ActivityTimeline';
import { useChannel } from './channel/useChannel';

export function App() {
  const [paletteOpen, setPaletteOpen] = useState(false);
  // A monotonically-bumped signal the palette / nav uses to focus the CEO input.
  const [ceoFocus, setCeoFocus] = useState(0);

  // One shared CEO channel across the command bar + Talk-to-CEO pane so a thread
  // started in the bar continues in the pane and vice-versa.
  const channel = useChannel();

  const openPalette = useCallback(() => setPaletteOpen(true), []);
  const closePalette = useCallback(() => setPaletteOpen(false), []);
  const focusCeo = useCallback(() => setCeoFocus((n) => n + 1), []);

  // Global ⌘K / Ctrl+K toggle.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setPaletteOpen((v) => !v);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  return (
    <div className="layout layout--rail">
      <Sidebar onOpenPalette={openPalette} />
      <main className="content">
        <Routes>
          <Route path="/" element={<Board />} />
          <Route
            path="/talk"
            element={<TalkToCeo channel={channel} focusSignal={ceoFocus} />}
          />
          <Route
            path="/needs-you"
            element={<Placeholder title="Needs You" hint="Act on approvals + blocked tasks from the Board's Needs-You column." />}
          />
          <Route path="/activity" element={<ActivityTimeline variant="pane" />} />
          <Route path="/projects" element={<Projects />} />
          <Route path="/autopilot" element={<Autopilot />} />
          <Route path="/agents" element={<Agents />} />
          <Route path="/usage" element={<Usage />} />
          <Route path="/runtimes" element={<Runtimes />} />
          <Route path="/live" element={<Live />} />
          <Route path="/settings" element={<Placeholder title="Settings" />} />
          <Route
            path="*"
            element={<Placeholder title="Not found" hint="This pane does not exist." />}
          />
        </Routes>
      </main>
      <ActivityTimeline variant="rail" />
      <CommandBar channel={channel} focusSignal={ceoFocus} />
      <CommandPalette
        open={paletteOpen}
        onClose={closePalette}
        onSendToCeo={focusCeo}
      />
    </div>
  );
}
