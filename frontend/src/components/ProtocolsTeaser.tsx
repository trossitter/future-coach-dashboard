import { type CSSProperties, useEffect, useRef, useState } from "react";

type ProtocolsTeaserProps = {
  onClose: () => void;
};

type ProtocolsStyle = CSSProperties & {
  "--protocol-progress": number;
  "--protocol-scrim-opacity": number;
  "--protocol-image-opacity": number;
  "--protocol-image-blur": string;
  "--protocol-image-saturate": number;
  "--protocol-image-contrast": number;
};

type ProtocolLineStyle = CSSProperties & {
  "--line-progress": number;
  "--line-y": string;
  "--line-blur": string;
};

type ProtocolCardStyle = CSSProperties & {
  "--protocol-card-progress": number;
  "--protocol-card-y": string;
  "--protocol-card-blur": string;
};

const clamp = (value: number, min: number, max: number) =>
  Math.min(max, Math.max(min, value));

const revealLine = (progress: number, start: number, end: number) =>
  clamp((progress - start) / (end - start), 0, 1);

export function ProtocolsTeaser({ onClose }: ProtocolsTeaserProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [progress, setProgress] = useState(0);

  const updateProgress = () => {
    const scroller = scrollRef.current;
    if (!scroller) return;

    const viewport = Math.max(1, scroller.clientHeight);
    const next = clamp((scroller.scrollTop - viewport * 0.35) / (viewport * 0.7), 0, 1);
    setProgress(Number(next.toFixed(3)));
  };

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    scrollRef.current?.scrollTo({ top: 0 });
    updateProgress();

    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };

    window.addEventListener("keydown", handleKey);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", handleKey);
    };
  }, [onClose]);

  const style: ProtocolsStyle = {
    "--protocol-progress": progress,
    "--protocol-scrim-opacity": progress * 0.92,
    "--protocol-image-opacity": 1 - progress * 0.3,
    "--protocol-image-blur": `${progress * 1.2}px`,
    "--protocol-image-saturate": 1 - progress * 0.16,
    "--protocol-image-contrast": 1 - progress * 0.08,
  };

  const lineStyle = (lineProgress: number): ProtocolLineStyle => ({
    "--line-progress": lineProgress,
    "--line-y": `${34 - lineProgress * 34}px`,
    "--line-blur": `${5 - lineProgress * 5}px`,
  });

  const cardProgress = revealLine(progress, 0.96, 1);
  const cardStyle: ProtocolCardStyle = {
    "--protocol-card-progress": cardProgress,
    "--protocol-card-y": `${34 - cardProgress * 34}px`,
    "--protocol-card-blur": `${5 - cardProgress * 5}px`,
  };

  return (
    <div className="protocols-page" role="dialog" aria-label="The handstand protocol">
      <button className="protocols-home" onClick={onClose}>
        Dashboard
      </button>
      <button className="protocols-close" onClick={onClose} aria-label="Back to dashboard">×</button>

      <div
        className="protocols-scroll"
        onScroll={updateProgress}
        ref={scrollRef}
        style={style}
      >
        <div className="protocols-scene">
          <div className="protocols-sticky-figure">
            <img src="/protocols-teaser-figure.png?v=2" alt="" />
            <div className="protocols-sticky-scrim" />
            <section className="protocols-title-stage" aria-label="The handstand protocol">
              <h1 aria-label="THE HANDSTAND PROTOCOL">
                <span className="protocols-title-line" style={lineStyle(revealLine(progress, 0.04, 0.34))}>
                  THE
                </span>
                <span className="protocols-title-line" style={lineStyle(revealLine(progress, 0.32, 0.66))}>
                  HANDSTAND
                </span>
                <span className="protocols-title-line" style={lineStyle(revealLine(progress, 0.62, 0.96))}>
                  PROTOCOL
                </span>
              </h1>
              <div
                className={`protocols-assign-card${cardProgress > 0.98 ? " is-visible" : ""}`}
                style={cardStyle}
              >
                <div className="protocols-assign-kicker">Science-backed protocols</div>
                <p>Adapt the Handstand protocol to any member, instantly, accurately, safely.</p>
                <button className="protocols-assign-cta" onClick={onClose}>
                  Assign
                </button>
              </div>
            </section>
          </div>
        </div>
      </div>
    </div>
  );
}
