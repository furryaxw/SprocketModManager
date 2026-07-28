const assert = require("node:assert/strict");
const { fetchRepositoryReleases } = require("../site/release-api.js");

function response(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() { return body; },
  };
}

async function testLatestFallback() {
  const calls = [];
  const release = { tag_name: "v1.3.1", assets: [{ name: "MelonLoaderTreeFix.dll" }] };
  const request = async (url) => {
    calls.push(url);
    return calls.length === 1 ? response(200, []) : response(200, release);
  };

  const releases = await fetchRepositoryReleases("furryaxw/MelonLoaderTreeFix", request);

  assert.deepEqual(releases, [release]);
  assert.match(calls[0], /\/releases\?per_page=100$/);
  assert.match(calls[1], /\/releases\/latest$/);
}

async function testNonEmptyListSkipsFallback() {
  const release = { tag_name: "v1.2.0", assets: [{ name: "TestMod.dll" }] };
  let calls = 0;
  const releases = await fetchRepositoryReleases("example/TestMod", async () => {
    calls += 1;
    return response(200, [release]);
  });

  assert.deepEqual(releases, [release]);
  assert.equal(calls, 1);
}

async function testMissingLatestReturnsEmptyList() {
  let calls = 0;
  const releases = await fetchRepositoryReleases("example/TestMod", async () => {
    calls += 1;
    return calls === 1 ? response(200, []) : response(404, { message: "Not Found" });
  });

  assert.deepEqual(releases, []);
}

Promise.all([
  testLatestFallback(),
  testNonEmptyListSkipsFallback(),
  testMissingLatestReturnsEmptyList(),
]).then(() => {
  process.stdout.write("site release API tests passed\n");
}).catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
