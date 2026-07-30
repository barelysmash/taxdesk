import { useEffect, useState } from "react";

const FEED_ID = import.meta.env.VITE_BEHOLD_FEED_ID || "";

export default function InstagramCard({
  handle = "barelysmash",
  caption = "Bar program · @barelysmash",
}) {
  const [posts, setPosts] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    if (!FEED_ID) return;
    fetch(`https://feeds.behold.so/${FEED_ID}`)
      .then(r => {
        if (!r.ok) throw new Error(`feed ${r.status}`);
        return r.json();
      })
      // Behold returns { posts: [...] } or a bare array depending on version
      .then(d => setPosts((d.posts ?? d ?? []).slice(0, 6)))
      .catch(e => setErr(String(e)));
  }, []);

  if (!FEED_ID) {
    return (
      <section className="card">
        <h2>Instagram · @{handle}</h2>
        <div className="sub">Behold feed not configured</div>
        <div className="empty" style={{ textAlign: "left", fontSize: 13 }}>
          Set <code className="mono">VITE_BEHOLD_FEED_ID</code> in
          {" "}<code className="mono">/etc/taxdesk/taxdesk.env</code> and rebuild
          the frontend to enable this card.
        </div>
      </section>
    );
  }

  return (
    <section className="card">
      <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
        <h2 style={{ flex: 1 }}>Instagram · @{handle}</h2>
        <a href={`https://instagram.com/${handle}`}
           target="_blank" rel="noreferrer"
           className="mono"
           style={{ fontSize: 10, letterSpacing: "0.15em",
                    textTransform: "uppercase",
                    color: "var(--oxblood)", textDecoration: "none" }}>
          open ↗
        </a>
      </div>
      <div className="sub">{caption}</div>

      {err && <div className="empty">Feed unavailable · {err}</div>}
      {!posts && !err && <div className="empty">Loading…</div>}

      {posts && (
        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(3, 1fr)",
          gap: 6,
        }}>
          {posts.map(p => <PostTile key={p.id ?? p.permalink} post={p} />)}
        </div>
      )}
    </section>
  );
}

function PostTile({ post }) {
  // Behold returns prefixed sizes (mediaUrl, sizes.small.mediaUrl, etc.).
  // Fall back gracefully across schema versions.
  const img =
    post.sizes?.medium?.mediaUrl ||
    post.sizes?.small?.mediaUrl  ||
    post.mediaUrl                ||
    post.thumbnailUrl;
  const href = post.permalink || `https://instagram.com/p/${post.id}`;
  const caption = (post.caption || "").slice(0, 120);

  return (
    <a href={href} target="_blank" rel="noreferrer"
       title={caption}
       style={{
         position: "relative",
         display: "block",
         aspectRatio: "1 / 1",
         overflow: "hidden",
         border: "1px solid var(--rule)",
         background: "var(--bg)",
       }}>
      {img && (
        <img src={img} alt={caption}
             loading="lazy"
             style={{
               width: "100%", height: "100%", objectFit: "cover",
               display: "block", filter: "saturate(0.92)",
             }} />
      )}
      {post.mediaType === "VIDEO" && (
        <span className="mono" style={{
          position: "absolute", top: 4, right: 4,
          background: "var(--ink)", color: "var(--bg-card)",
          fontSize: 9, padding: "1px 4px", letterSpacing: "0.1em",
        }}>VID</span>
      )}
    </a>
  );
}
