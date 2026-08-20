"use strict";

async function loadRegistry(forceRefresh) {
    setRegistryStatus("loadingRegistry");
    setSystemState("refreshingRegistry");
    elements.refresh.disabled = true;
    elements.refresh.querySelector("svg")?.classList.add("spin");
    try {
        const response = await fetch("./index.json", {cache: "no-store"});
        if (!response.ok) throw new Error(`Registry HTTP ${response.status}`);
        const registry = await response.json();
        state.packages = Array.isArray(registry.packages) ? registry.packages : [];
        state.releases.clear();
        state.packages.forEach((pkg) => {
            const release = Array.isArray(pkg.releases) ? pkg.releases[0] : null;
            state.releases.set(pkg.id, normalizeEmbeddedRelease(release));
        });
        updateCategoryCounts();
        elements.packageCount.textContent = String(state.packages.length);
        elements.releaseCount.textContent = String([...state.releases.values()].filter(Boolean).length);
        setRegistryStatus("registryUpdated", {time: formatTime(registry.generated_at)});
        setSystemState("registryOnline");
        renderPackages();
    } catch (error) {
        setRegistryStatus("loadFailed", {message: error.message});
        setSystemState("registryError", true);
        state.packages = [];
        updateCategoryCounts();
        renderPackages();
    } finally {
        elements.refresh.disabled = false;
        elements.refresh.querySelector("svg")?.classList.remove("spin");
    }
}

function normalizeEmbeddedRelease(release) {
    if (!release || typeof release !== "object") return null;
    const parsedVersion = parseSemver(release.version);
    if (!parsedVersion || !Array.isArray(release.assets) || !release.assets.length) return null;
    return {
        ...release,
        tag_name: release.tag,
        html_url: release.page_url,
        parsedVersion,
        selectedAssets: release.assets,
    };
}

function parseSemver(value) {
    const match = String(value).match(/^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$/);
    return match ? {
        major: +match[1],
        minor: +match[2],
        patch: +match[3],
        prerelease: match[4] ? match[4].split(".") : []
    } : null;
}

function compareSemver(left, right) {
    for (const field of ["major", "minor", "patch"]) if (left[field] !== right[field]) return left[field] - right[field];
    if (!left.prerelease.length && right.prerelease.length) return 1;
    if (left.prerelease.length && !right.prerelease.length) return -1;
    for (let index = 0; index < Math.max(left.prerelease.length, right.prerelease.length); index += 1) {
        if (left.prerelease[index] === undefined) return -1;
        if (right.prerelease[index] === undefined) return 1;
        const a = left.prerelease[index];
        const b = right.prerelease[index];
        if (a === b) continue;
        const an = /^\d+$/.test(a);
        const bn = /^\d+$/.test(b);
        if (an && bn) return Number(a) - Number(b);
        if (an !== bn) return an ? -1 : 1;
        return a.localeCompare(b);
    }
    return 0;
}

function updateCategoryCounts() {
    const counts = {all: state.packages.length};
    state.packages.forEach((pkg) => {
        counts[pkg.category] = (counts[pkg.category] || 0) + 1;
    });
    document.querySelectorAll("[data-category]").forEach((button) => {
        const output = button.querySelector("b");
        if (output) output.textContent = String(counts[button.dataset.category] || 0);
    });
}

function filteredPackages() {
    const packages = state.packages.filter((pkg) => {
        if (state.category !== "all" && pkg.category !== state.category) return false;
        if (!state.query) return true;
        const text = [
            pkg.id, pkg.name, pkg.repository, ...pkg.authors, ...pkg.tags,
            ...Object.values(pkg.display_name || {}), ...Object.values(pkg.description || {}),
        ].join(" ").toLocaleLowerCase();
        return text.includes(state.query);
    });
    return packages.sort((left, right) => {
        const featured = Number(Boolean(right.featured)) - Number(Boolean(left.featured));
        if (featured) return featured;
        if (state.sort === "release") {
            const a = state.releases.get(left.id)?.parsedVersion;
            const b = state.releases.get(right.id)?.parsedVersion;
            if (a && b) return compareSemver(b, a);
            if (a) return -1;
            if (b) return 1;
        }
        if (state.sort === "category") {
            const category = left.category.localeCompare(right.category);
            if (category) return category;
        }
        return localized(left.display_name).localeCompare(localized(right.display_name), state.language);
    });
}

function renderPackages() {
    if (!elements.grid) return;
    const packages = filteredPackages();
    elements.resultCount.textContent = String(packages.length);
    elements.grid.replaceChildren(...packages.map(renderCard));
    elements.empty.hidden = packages.length !== 0;
    refreshIcons();
}

function renderCard(pkg) {
    const release = state.releases.get(pkg.id);
    const article = document.createElement("article");
    article.className = "mod-card";
    article.dataset.packageId = pkg.id;
    article.tabIndex = 0;
    article.setAttribute("role", "button");
    article.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            openDetails(pkg.id);
        }
    });
    const avatar = `https://github.com/${pkg.repository.split("/")[0]}.png?size=128`;
    const releaseText = release === undefined ? tr("checking") : release ? release.tag_name : tr("unavailable");
    const pending = release ? "" : " pending";
    const dependencies = tr("dependencyCount", {count: pkg.dependencies.length});
    const description = localized(pkg.description);
    const featuredMark = pkg.featured
        ? `<span class="featured-star" role="img" aria-label="${escapeAttribute(tr("starterRecommended"))}" title="${escapeAttribute(tr("starterRecommended"))}">★</span>`
        : "";
    article.innerHTML = `
    <img class="mod-avatar" src="${escapeAttribute(avatar)}" alt="${escapeAttribute(pkg.authors.join(", "))}" loading="lazy" />
    <div class="mod-identity">
      <h2>${featuredMark}${escapeHtml(localized(pkg.display_name))}</h2>
      <span class="category-badge">${escapeHtml(categoryLabel(pkg.category))}</span>
    </div>
    <p class="mod-description"${description ? "" : " hidden"}>${escapeHtml(description)}</p>
    <div class="mod-metadata">
      <span class="repository"><i data-lucide="github"></i>${escapeHtml(pkg.repository)}</span>
      <span><i data-lucide="scale"></i>${escapeHtml(pkg.license)}</span>
      <span><i data-lucide="split"></i>${escapeHtml(dependencies)}</span>
    </div>
    <div class="release-column">
      <span class="release-badge${pending}">${escapeHtml(releaseText)}</span>
      <span class="verified-label"><i data-lucide="shield-check"></i>${escapeHtml(tr("verified"))}</span>
      <span class="card-arrow"><i data-lucide="chevron-right"></i></span>
    </div>`;
    return article;
}

function openDetails(packageId) {
    state.selectedPackageId = packageId;
    renderDetails(packageId);
    if (!elements.detail.open) elements.detail.showModal();
}

function renderDetails(packageId) {
    const pkg = state.packages.find((item) => item.id === packageId);
    if (!pkg) return;
    const release = state.releases.get(pkg.id);
    const owner = pkg.repository.split("/")[0];
    document.querySelector("#detail-avatar").src = `https://github.com/${owner}.png?size=160`;
    document.querySelector("#detail-avatar").alt = pkg.authors.join(", ");
    document.querySelector("#detail-title").textContent = localized(pkg.display_name);
    document.querySelector("#detail-repository").textContent = pkg.repository;
    const featured = document.querySelector("#detail-featured");
    featured.textContent = `★ ${tr("starterRecommended")}`;
    featured.hidden = !pkg.featured;
    const description = localized(pkg.description);
    const descriptionNode = document.querySelector("#detail-description");
    descriptionNode.textContent = description;
    descriptionNode.hidden = !description;
    const details = [
        [tr("version"), release?.tag_name || tr("unavailable")],
        [tr("authors"), pkg.authors.join(", ")],
        [tr("license"), pkg.license],
        [tr("category"), categoryLabel(pkg.category)],
        [tr("assets"), release?.selectedAssets.map((asset) => asset.name).join(", ") || "-"],
        [tr("packageId"), pkg.id],
    ];
    document.querySelector("#detail-grid").innerHTML = details
        .map(([term, value]) => `<div><dt>${escapeHtml(term)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("");
    document.querySelector("#detail-dependencies").innerHTML = pkg.dependencies.length
        ? pkg.dependencies.map((item) => `<span class="dependency-pill">${escapeHtml(item.id)} ${escapeHtml(item.version)}</span>`).join("")
        : `<span class="dependency-pill">${escapeHtml(tr("none"))}</span>`;
    const recommendations = pkg.recommendations || [];
    document.querySelector("#detail-recommendations").innerHTML = recommendations.length
        ? recommendations.map((item) => `<span class="dependency-pill">${escapeHtml(item)}</span>`).join("")
        : `<span class="dependency-pill">${escapeHtml(tr("none"))}</span>`;
    document.querySelector("#detail-repo-link").href = `https://github.com/${pkg.repository}`;
    const releaseLink = document.querySelector("#detail-release-link");
    releaseLink.href = release?.html_url || `https://github.com/${pkg.repository}/releases`;
    releaseLink.toggleAttribute("aria-disabled", !release);
    refreshIcons();
}

function categoryLabel(category) {
    const key = `category${category.charAt(0).toUpperCase()}${category.slice(1)}`;
    return tr(key);
}
