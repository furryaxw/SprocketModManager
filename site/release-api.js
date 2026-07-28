(function exposeReleaseApi(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.SprocketReleaseApi = api;
}(typeof globalThis !== "undefined" ? globalThis : this, () => {
  const headers = { Accept: "application/vnd.github+json" };

  async function readJson(response, endpoint) {
    if (!response.ok) throw new Error(`GitHub HTTP ${response.status} for ${endpoint}`);
    return response.json();
  }

  async function fetchRepositoryReleases(repository, request = fetch) {
    const parts = String(repository).split("/");
    if (parts.length !== 2 || parts.some((part) => !part)) throw new Error("Invalid GitHub repository");
    const encoded = parts.map(encodeURIComponent).join("/");
    const base = `https://api.github.com/repos/${encoded}/releases`;
    const listEndpoint = `${base}?per_page=100`;
    const releases = await readJson(await request(listEndpoint, { headers }), listEndpoint);
    if (!Array.isArray(releases)) throw new Error("GitHub Releases response is not a list");
    if (releases.length) return releases;

    const latestEndpoint = `${base}/latest`;
    const latestResponse = await request(latestEndpoint, { headers });
    if (latestResponse.status === 404) return [];
    const latest = await readJson(latestResponse, latestEndpoint);
    return latest && typeof latest === "object" && !Array.isArray(latest) ? [latest] : [];
  }

  return { fetchRepositoryReleases };
}));
