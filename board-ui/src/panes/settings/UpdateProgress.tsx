const LABELS: Record<string, string> = {
  checking: 'Checking releases',
  backing_up: 'Backing up the database',
  downloading: 'Downloading update',
  verifying: 'Verifying release signatures',
  installing: 'Installing update',
  switching: 'Switching engine release',
  restarting: 'Restarting engine — reconnecting',
};

export function UpdateProgress({ phase }: { phase: string }) {
  const label = LABELS[phase];
  if (!label) return null;
  return (
    <div role="status" aria-live="polite">
      <p className="settings__meta">{label}</p>
      <progress aria-label={label} style={{ width: '100%' }} />
      <p className="settings__hint">Stage progress only. The engine does not report a download percentage.</p>
    </div>
  );
}
