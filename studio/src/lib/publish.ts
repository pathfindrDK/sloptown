// Which publish action fits the Vibe now (the studio's top bar and Miira's, via drive:toolbar).
export type PublishState = "publish" | "update" | "published";

/** "publish": nothing published yet; "update": published, but there are newer
 *  changes; "published": the latest version is what's published. */
export function publishState(headSha: string | null, publishedSha: string | null): PublishState {
  if (!publishedSha) return "publish";
  return headSha && headSha !== publishedSha ? "update" : "published";
}
