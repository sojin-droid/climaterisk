import { useEffect, useState } from "react";

/**
 * A peril's methodology figure, served by `GET /api/libraries/method-figure/{peril}.png`.
 *
 * The figure is produced by that peril's analysis script, not by a run, so it may simply
 * not exist yet — in that case the backend 404s and this component renders nothing rather
 * than a broken image. Click to open the full-resolution PNG in a new tab.
 */
export function MethodFigure({
  peril,
  caption,
}: {
  peril: string;
  /** One line on what the panels show; omitted if absent. */
  caption?: string;
}) {
  const url = `/api/libraries/method-figure/${peril}.png`;
  const [state, setState] = useState<"loading" | "ok" | "missing">("loading");

  useEffect(() => {
    let alive = true;
    setState("loading");
    // Probe with GET (the route only declares GET) so a missing figure is detected without
    // the browser logging a broken-image error. The response is cached for the <img>.
    fetch(url)
      .then((r) => alive && setState(r.ok ? "ok" : "missing"))
      .catch(() => alive && setState("missing"));
    return () => {
      alive = false;
    };
  }, [url]);

  if (state !== "ok") return null;
  return (
    <div style={{ marginTop: 14 }}>
      <div className="section-title" style={{ marginBottom: 6 }}>
        How this peril is modelled
      </div>
      <a href={url} target="_blank" rel="noreferrer" title="Open full resolution">
        <img
          src={url}
          alt={`${peril.replace(/_/g, " ")} methodology figure`}
          style={{
            width: "100%",
            height: "auto",
            display: "block",
            borderRadius: 6,
            border: "1px solid var(--border)",
            background: "#fff",
          }}
        />
      </a>
      {caption && (
        <p className="hint" style={{ marginTop: 6 }}>
          {caption}
        </p>
      )}
    </div>
  );
}
