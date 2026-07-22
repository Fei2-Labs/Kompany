// Settings pane — the board SPA's counterpart to the legacy
// /ui/settings.html cyberpunk-terminal page. Each section is an
// independently-loading card (own useAsync + save handler) rather than one
// big blocking fetch, so a slow/erroring section never blocks the rest —
// this is also why the founder previously only saw the Telegram card:
// it was the only one ported here. Order mirrors the legacy page.

import { ModelCard } from './settings/ModelCard';
import { ModelSourceCard } from './settings/ModelSourceCard';
import { ResendCard } from './settings/ResendCard';
import { EmailSmtpCard } from './settings/EmailSmtpCard';
import { TelegramCard } from './settings/TelegramCard';
import { BrowserCard } from './settings/BrowserCard';
import { FounderProfileCard } from './settings/FounderProfileCard';
import { FounderRulesCard } from './settings/FounderRulesCard';
import { IntegrationsCard } from './settings/IntegrationsCard';
import { CredentialsCard } from './settings/CredentialsCard';
import { WorkspacesCard } from './settings/WorkspacesCard';

export function Settings() {
  return (
    <section className="pane">
      <header className="pane__header">
        <h1 className="pane__title">Settings</h1>
        <p className="pane__subtitle">
          Model, integrations, founder profile + rules — verified before
          anything hits the vault or the live engine.
        </p>
      </header>

      <div className="settings">
        <ModelCard />
        <ModelSourceCard />
        <ResendCard />
        <EmailSmtpCard />
        <TelegramCard />
        <BrowserCard />
        <FounderProfileCard />
        <FounderRulesCard />
        <IntegrationsCard />
        <CredentialsCard />
        <WorkspacesCard />
      </div>
    </section>
  );
}
