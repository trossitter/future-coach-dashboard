export function ProtocolsTeaser({ onClose }: { onClose: () => void }) {
  return (
    <div className="protocols-page protocols-teaser" role="dialog" aria-label="Coming soon: protocols">
      <button className="protocols-close" onClick={onClose} aria-label="Back to dashboard">×</button>

      <div className="protocols-teaser-figure" aria-hidden="true">
        <img src="/protocols-teaser.png" alt="" />
        <div className="protocols-teaser-scrim" />
      </div>

      <div className="protocols-teaser-copy">
        <div className="eyebrow">COMING SOON</div>
        <h2>THE HANDSTAND PROTOCOL</h2>
        <p>
          Skill progressions, strength work, and coach judgment gathered into one
          assignable protocol — adapt the Handstand, Brady, or Biles protocol to any
          member, safely…
        </p>
      </div>
    </div>
  );
}
