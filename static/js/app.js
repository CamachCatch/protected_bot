const REFRESH_INTERVAL_MS = 15000;
const fallbackAvatar = "https://cdn.discordapp.com/embed/avatars/0.png";

let guilds = [];
let allMembers = [];
let activeTab = "guilds";
let guildRoleCache = {};
let isRefreshing = false;
let refreshTimer = null;
let logSearchTimer = null;

const LOG_PRESETS = [
    { label: "Все", category: "", level: "", source: "" },
    { label: "Защита", category: "security", level: "", source: "" },
    { label: "Модерация", category: "moderation", level: "", source: "" },
    { label: "Доступы", category: "access", level: "", source: "" },
    { label: "Роли", category: "roles", level: "", source: "" },
    { label: "Критичные", category: "", level: "danger", source: "" }
];

const signatures = {
    guilds: "",
    members: "",
    autorole: "",
    whitelist: "",
    blacklist: "",
    protected: "",
    bans: "",
    selectors: ""
};

document.addEventListener("DOMContentLoaded", () => {
    loadGuilds(true);
    startLiveRefresh();
});

function qs(id) {
    return document.getElementById(id);
}

function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

function setText(id, value) {
    const node = qs(id);
    if (node) node.textContent = value;
}

function setState(container, className, text) {
    if (!container) return;
    container.innerHTML = "";
    container.appendChild(el("div", className, text));
}

async function fetchJson(url, options) {
    const response = await fetch(url, {
        cache: "no-store",
        ...options
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
}

function signature(data) {
    return JSON.stringify(data);
}

function changed(key, data, force = false) {
    const next = signature(data);
    if (!force && signatures[key] === next) return false;
    signatures[key] = next;
    return true;
}

function formatNumber(value) {
    return new Intl.NumberFormat("ru-RU").format(Number(value || 0));
}

function startLiveRefresh() {
    if (refreshTimer) clearInterval(refreshTimer);
    refreshTimer = setInterval(() => refreshActiveTab(false), REFRESH_INTERVAL_MS);
    document.addEventListener("visibilitychange", () => {
        if (!document.hidden) refreshActiveTab(false);
    });
}

async function refreshActiveTab(manual = false) {
    if (isRefreshing) return;
    isRefreshing = true;
    try {
        if (activeTab === "guilds") {
            await loadGuilds(false, manual);
        } else {
            await refreshGuildsForSelectors(false);
            if (activeTab === "manage") await loadMembers(false, manual);
            if (activeTab === "autorole") await loadAutoRole(false, manual);
            if (activeTab === "whitelist") await loadWhitelist(false, manual);
            if (activeTab === "blacklist") await loadBlacklist(false, manual);
            if (activeTab === "protected") await loadProtected(false, manual);
            if (activeTab === "bans") await loadBans(false, manual);
            if (activeTab === "logs") await loadLogs(false, manual);
        }
        markUpdated();
    } catch (error) {
        console.error("Refresh error:", error);
        if (manual) showNotification("Не удалось обновить данные", "error");
    } finally {
        isRefreshing = false;
    }
}

function showTab(tabName) {
    activeTab = tabName;
    document.querySelectorAll(".tab-content").forEach(content => content.classList.remove("active"));
    document.querySelectorAll(".tab").forEach(tab => tab.classList.toggle("active", tab.dataset.tab === tabName));
    qs(`${tabName}-content`)?.classList.add("active");
    refreshActiveTab(false);
}

function markUpdated() {
    return true;
}

function updateMetrics() {
    const totalMembers = guilds.reduce((sum, guild) => sum + Number(guild.member_count || 0), 0);
    const selected = getSelectedGuild();
    setText("metric-guilds", formatNumber(guilds.length));
    setText("metric-members", formatNumber(totalMembers));
    setText("metric-selected", selected ? selected.name : "-");
}

function getSelectedGuild() {
    const activeSelect = qs(`${activeTab}-guild`);
    const fallbackIds = ["manage-guild", "autorole-guild", "whitelist-guild", "blacklist-guild", "protected-guild", "bans-guild", "logs-guild"];
    const selectedId = activeSelect?.value || fallbackIds.map(id => qs(id)?.value).find(Boolean);
    return guilds.find(guild => guild.id === selectedId);
}

async function loadGuilds(showLoading = true, forceRender = false) {
    const container = qs("guilds-list");
    if (showLoading) setState(container, "loading", "Загрузка серверов...");

    try {
        const data = await fetchJson("/api/guilds");
        guilds = Array.isArray(data) ? data : [];
        fillGuildSelectors();
        updateMetrics();

        if (!guilds.length) {
            if (showLoading || forceRender || !container.children.length) setState(container, "empty", "Серверы не найдены");
            return;
        }

        if (changed("guilds", guilds, forceRender || showLoading)) {
            renderGuilds(container);
        }
        markUpdated();
    } catch (error) {
        console.error("Error loading guilds:", error);
        if (showLoading || !container.children.length) setState(container, "empty", "Ошибка загрузки серверов");
    }
}

async function refreshGuildsForSelectors(forceRender = false) {
    const data = await fetchJson("/api/guilds");
    guilds = Array.isArray(data) ? data : [];
    fillGuildSelectors(forceRender);
    updateMetrics();
}

function fillGuildSelectors(forceRender = false) {
    const selectorSignature = signature(guilds.map(guild => [guild.id, guild.name]));
    if (!forceRender && signatures.selectors === selectorSignature) return;
    signatures.selectors = selectorSignature;

    ["whitelist-guild", "blacklist-guild", "protected-guild", "bans-guild", "manage-guild", "autorole-guild", "logs-guild"].forEach(selectorId => {
        const select = qs(selectorId);
        if (!select) return;
        const previous = select.value;
        select.innerHTML = "";
        select.appendChild(new Option("Выберите сервер...", ""));
        guilds.forEach(guild => select.appendChild(new Option(guild.name, guild.id)));
        if (previous && guilds.some(guild => guild.id === previous)) select.value = previous;
    });
}

function renderGuilds(container) {
    container.innerHTML = "";
    const grid = el("div", "guild-grid");
    guilds.forEach(guild => {
        const card = el("button", "guild-card");
        card.type = "button";
        card.addEventListener("click", () => openGuildModal(guild.id));

        const header = el("div", "guild-header");
        const img = el("img", "guild-icon");
        img.src = guild.icon || fallbackAvatar;
        img.alt = guild.name;
        img.loading = "lazy";

        const info = el("div", "guild-info");
        info.append(el("h3", "", guild.name), el("div", "guild-stats", `${formatNumber(guild.member_count)} участников`));
        header.append(img, info);

        const footer = el("div", "guild-card-footer");
        footer.append(el("span", "pill", `ID ${guild.id}`), el("span", "pill", `Owner ${guild.owner_id}`));
        card.append(header, footer);
        grid.appendChild(card);
    });
    container.appendChild(grid);
}

async function getGuildRoles(guildId, force = false) {
    if (!force && guildRoleCache[guildId]) return guildRoleCache[guildId];
    const details = await fetchJson(`/api/guild/${guildId}/details`);
    guildRoleCache[guildId] = details.roles || [];
    return guildRoleCache[guildId];
}

async function openGuildModal(guildId) {
    const modal = qs("guild-modal");
    const title = qs("modal-guild-name");
    const body = qs("modal-guild-details");
    if (!modal || !body) return;

    modal.classList.add("active");
    setState(body, "loading", "Загрузка...");

    try {
        const details = await fetchJson(`/api/guild/${guildId}/details`);
        if (title) title.textContent = details.name;
        guildRoleCache[guildId] = details.roles || [];
        renderGuildDetails(body, details);
    } catch (error) {
        console.error("Error loading guild details:", error);
        setState(body, "empty", "Не удалось загрузить детали сервера");
    }
}

function renderGuildDetails(container, details) {
    container.innerHTML = "";
    const grid = el("div", "info-grid");
    [
        ["Участники", details.member_count],
        ["Онлайн", details.online_count],
        ["Боты", details.bot_count],
        ["Текстовые", details.text_channels],
        ["Голосовые", details.voice_channels],
        ["Роли", (details.roles || []).length],
        ["Бусты", details.boost_count],
        ["Уровень", details.boost_level]
    ].forEach(([label, value]) => {
        const card = el("div", "info-card");
        card.append(el("div", "info-label", label), el("div", "info-value", formatNumber(value)));
        grid.appendChild(card);
    });

    const title = el("h3", "section-title", "Роли сервера");
    title.style.margin = "4px 0 12px";

    const list = el("div", "roles-list");
    (details.roles || []).forEach(role => {
        const item = el("button", "role-item");
        item.type = "button";
        item.addEventListener("click", () => copyToClipboard(role.id));

        const left = el("div");
        const name = el("div", "role-name");
        const swatch = el("span", "role-swatch");
        swatch.style.backgroundColor = role.color || "#8b5cf6";
        name.append(swatch, document.createTextNode(role.name));
        left.append(name, el("div", "role-id", role.id));
        item.append(left, el("span", "pill", `${formatNumber(role.members)} чел.`));
        list.appendChild(item);
    });

    container.append(grid, title, list);
}

async function loadMembers(showLoading = true, forceRender = false) {
    const guildId = qs("manage-guild")?.value;
    const container = qs("manage-list");
    if (!guildId) {
        allMembers = [];
        setState(container, "empty", "Выберите сервер");
        updateMetrics();
        return;
    }

    if (showLoading) setState(container, "loading", "Загрузка участников...");
    try {
        const [members] = await Promise.all([
            fetchJson(`/api/members/${guildId}`),
            getGuildRoles(guildId)
        ]);
        allMembers = Array.isArray(members) ? members : [];
        updateMetrics();

        if (!allMembers.length) {
            if (showLoading || forceRender || !container.children.length) setState(container, "empty", "Участники не найдены");
            return;
        }

        if (changed("members", { guildId, members: allMembers }, forceRender || showLoading)) {
            filterMembers();
        }
        markUpdated();
    } catch (error) {
        console.error("Error loading members:", error);
        if (showLoading || !container.children.length) setState(container, "empty", "Ошибка загрузки участников");
    }
}

function filterMembers() {
    const search = (qs("member-search")?.value || "").toLowerCase().trim();
    const filtered = allMembers.filter(member => {
        return [member.username, member.display_name, member.user_id]
            .filter(Boolean)
            .join(" ")
            .toLowerCase()
            .includes(search);
    });
    displayMembers(filtered);
}

function displayMembers(members) {
    const container = qs("manage-list");
    container.innerHTML = "";
    if (!members.length) {
        setState(container, "empty", "Ничего не найдено");
        return;
    }
    const list = el("div", "member-list");
    members.forEach(member => list.appendChild(createMemberCard(member)));
    container.appendChild(list);
}

function createMemberCard(member) {
    const guildId = qs("manage-guild").value;
    const card = el("div", "member-card");
    card.dataset.userId = member.user_id;

    const header = el("div", "member-header");
    const avatar = el("img", "member-avatar");
    avatar.src = member.avatar || fallbackAvatar;
    avatar.alt = member.username || "avatar";
    avatar.loading = "lazy";

    const info = el("div", "member-info");
    info.append(el("div", "member-name", member.display_name || member.username));
    info.append(el("div", "member-id", `@${member.username} • ${member.user_id} • вход ${member.joined_at || "N/A"}`));

    const roles = el("div", "roles-container");
    if (member.roles && member.roles.length) {
        member.roles.slice(0, 14).forEach(role => {
            const badge = el("button", "role-badge", role.name);
            badge.type = "button";
            badge.title = `Скопировать ID: ${role.id}`;
            badge.addEventListener("click", () => copyToClipboard(role.id));
            roles.appendChild(badge);
        });
        if (member.roles.length > 14) roles.appendChild(el("span", "pill", `+${member.roles.length - 14}`));
    } else {
        roles.appendChild(el("span", "muted", "Нет ролей"));
    }
    info.appendChild(roles);

    const statuses = el("div", "status-container");
    let statusCount = 0;
    if (member.access_level) { statuses.appendChild(badge(member.access_level, "status-access")); statusCount++; }
    if (member.is_whitelist) { statuses.appendChild(badge("Whitelist", "status-wl")); statusCount++; }
    if (member.is_protected) { statuses.appendChild(badge("Protected", "status-prot")); statusCount++; }
    if (member.is_blacklist) { statuses.appendChild(badge("Blacklist", "status-bl")); statusCount++; }
    if (member.ban_info) { statuses.appendChild(badge(`Banned ${member.ban_info.type}`, "status-ban")); statusCount++; }
    if (!statusCount) statuses.appendChild(el("span", "muted", "Без специальных статусов"));
    info.appendChild(statuses);

    if (member.ban_info) info.appendChild(el("div", "small-note", `Бан истекает: ${member.ban_info.expire}`));

    const roleControls = el("div", "role-control-row");
    const select = el("select", "role-select");
    select.appendChild(new Option("Выберите роль...", ""));
    (guildRoleCache[guildId] || []).forEach(role => select.appendChild(new Option(role.name, role.id)));

    const giveRole = el("button", "action-btn btn-access", "Выдать роль");
    giveRole.type = "button";
    giveRole.addEventListener("click", () => performAction("give_role", guildId, member.user_id, null, select.value));

    const removeRole = el("button", "action-btn btn-remove", "Снять роль");
    removeRole.type = "button";
    removeRole.addEventListener("click", () => performAction("remove_role", guildId, member.user_id, null, select.value));
    roleControls.append(select, giveRole, removeRole);
    info.appendChild(roleControls);

    const actions = el("div", "actions-container");
    if (!member.access_level) {
        ["low", "mid", "pusy"].forEach(level => {
            const btn = el("button", "action-btn btn-access", level);
            btn.type = "button";
            btn.addEventListener("click", () => performAction("add_access", guildId, member.user_id, level));
            actions.appendChild(btn);
        });
    } else {
        actions.appendChild(actionButton("remove_access", "Снять доступ", "btn-remove", guildId, member.user_id));
    }

    actions.appendChild(actionButton(member.is_whitelist ? "remove_whitelist" : "add_whitelist", member.is_whitelist ? "Убрать WL" : "В WL", member.is_whitelist ? "btn-remove" : "btn-wl", guildId, member.user_id));
    actions.appendChild(actionButton(member.is_blacklist ? "remove_blacklist" : "add_blacklist", member.is_blacklist ? "Убрать ЧС" : "В ЧС", member.is_blacklist ? "btn-remove" : "btn-bl", guildId, member.user_id));
    actions.appendChild(actionButton(member.is_protected ? "remove_protected" : "add_protected", member.is_protected ? "Снять защиту" : "Защитить", member.is_protected ? "btn-remove" : "btn-prot", guildId, member.user_id));
    if (member.ban_info) {
        actions.appendChild(actionButton("unban", "Разбанить", "btn-remove", guildId, member.user_id));
    } else {
        actions.appendChild(banButton(guildId, member.user_id, "pusy", "Pusy ban"));
        actions.appendChild(banButton(guildId, member.user_id, "apusy", "APusy ban"));
    }
    info.appendChild(actions);

    header.append(avatar, info);
    card.appendChild(header);
    return card;
}

function badge(text, className) {
    return el("span", `status-badge ${className}`, text);
}

function actionButton(action, text, className, guildId, userId) {
    const btn = el("button", `action-btn ${className}`, text);
    btn.type = "button";
    btn.addEventListener("click", () => performAction(action, guildId, userId));
    return btn;
}

function banButton(guildId, userId, banType, text) {
    const btn = el("button", "action-btn btn-ban", text);
    btn.type = "button";
    btn.addEventListener("click", () => banUser(guildId, userId, banType));
    return btn;
}

async function performAction(action, guildId, userId, level, roleId) {
    if ((action === "give_role" || action === "remove_role") && !roleId) {
        showNotification("Выберите роль", "error");
        return;
    }

    const data = { action, guild_id: guildId, user_id: userId };
    if (level) data.level = level;
    if (roleId) data.role_id = roleId;

    try {
        const result = await fetchJson("/api/action", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data)
        });
        if (result.success) {
            invalidateActionCaches();
            showNotification(result.message, "success");
            await refreshAfterAction();
        } else {
            showNotification(`Ошибка: ${result.error}`, "error");
        }
    } catch (error) {
        console.error("Action error:", error);
        showNotification("Ошибка выполнения действия", "error");
    }
}

async function banUser(guildId, userId, banType) {
    const duration = prompt(`Введите длительность ${banType} бана, например 1h, 30m, 7d:`, "1d");
    if (!duration) return;

    try {
        const result = await fetchJson("/api/action", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action: "ban", guild_id: guildId, user_id: userId, duration, ban_type: banType })
        });
        if (result.success) {
            invalidateActionCaches();
            showNotification(result.message, "success");
            await refreshAfterAction();
        } else {
            showNotification(`Ошибка: ${result.error}`, "error");
        }
    } catch (error) {
        console.error("Ban error:", error);
        showNotification("Ошибка выдачи бана", "error");
    }
}

async function loadAutoRole(showLoading = true, forceRender = false) {
    const guildId = qs("autorole-guild")?.value;
    const container = qs("autorole-settings");
    if (!guildId) {
        setState(container, "empty", "Выберите сервер");
        updateMetrics();
        return;
    }
    if (!showLoading && container?.querySelector("input:focus, button:focus")) return;
    if (showLoading) setState(container, "loading", "Загрузка настроек...");

    try {
        const [settings, roles] = await Promise.all([
            fetchJson(`/api/autorole/${guildId}`),
            getGuildRoles(guildId)
        ]);
        const data = { guildId, settings, roles };
        if (changed("autorole", data, forceRender || showLoading)) {
            renderAutoRole(container, settings, roles);
        }
        updateMetrics();
        markUpdated();
    } catch (error) {
        console.error("Autorole error:", error);
        if (showLoading || !container.children.length) setState(container, "empty", "Ошибка загрузки настроек");
    }
}

function renderAutoRole(container, settings, roles) {
    container.innerHTML = "";
    const box = el("div", "autorole-settings-box");
    const toggleRow = el("div", "autorole-toggle");
    const text = el("div");
    text.append(el("div", "member-name", "Автоматическая выдача ролей"), el("div", "small-note", "Отметьте роли, которые получит новый участник."));

    const toggle = el("button", `toggle-switch ${settings.enabled ? "active" : ""}`);
    toggle.id = "autorole-toggle";
    toggle.type = "button";
    toggle.setAttribute("aria-label", "Переключить автороль");
    toggle.appendChild(el("span", "toggle-slider"));
    toggle.addEventListener("click", () => toggle.classList.toggle("active"));
    toggleRow.append(text, toggle);

    const list = el("div", "autorole-role-list");
    roles.forEach(role => {
        const label = el("label", "role-check");
        const input = document.createElement("input");
        input.type = "checkbox";
        input.value = role.id;
        input.checked = (settings.role_ids || []).includes(role.id);
        const swatch = el("span", "role-swatch");
        swatch.style.backgroundColor = role.color || "#8b5cf6";
        label.append(input, swatch, document.createTextNode(role.name));
        list.appendChild(label);
    });

    const save = el("button", "autorole-save-btn", "Сохранить настройки");
    save.type = "button";
    save.addEventListener("click", saveAutoRole);
    box.append(toggleRow, list, save, el("div", "autorole-info", "Можно выбрать несколько ролей. Изменения применяются после сохранения."));
    container.appendChild(box);
}

async function saveAutoRole() {
    const guildId = qs("autorole-guild").value;
    const enabled = qs("autorole-toggle").classList.contains("active");
    const roleIds = Array.from(document.querySelectorAll(".autorole-role-list input:checked")).map(input => input.value);
    if (enabled && !roleIds.length) {
        showNotification("Выберите хотя бы одну роль", "error");
        return;
    }

    try {
        const result = await fetchJson(`/api/autorole/${guildId}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ enabled, role_ids: roleIds })
        });
        if (result.success) {
            signatures.autorole = "";
            showNotification(result.message, "success");
            await loadAutoRole(false, true);
        } else {
            showNotification(`Ошибка: ${result.error}`, "error");
        }
    } catch (error) {
        console.error("Save autorole error:", error);
        showNotification("Ошибка сохранения автороли", "error");
    }
}

async function loadWhitelist(showLoading = true, forceRender = false) {
    await loadUserList("whitelist", "whitelist-guild", "whitelist-list", "/api/whitelist/", "Вайтлист пуст", showLoading, forceRender, false, "remove_whitelist");
}

async function loadBlacklist(showLoading = true, forceRender = false) {
    await loadUserList("blacklist", "blacklist-guild", "blacklist-list", "/api/blacklist/", "Черный список пуст", showLoading, forceRender, false, "remove_blacklist");
}

async function loadProtected(showLoading = true, forceRender = false) {
    await loadUserList("protected", "protected-guild", "protected-list", "/api/protected/", "Нет защищенных пользователей", showLoading, forceRender, false, "remove_protected");
}

async function loadBans(showLoading = true, forceRender = false) {
    await loadUserList("bans", "bans-guild", "bans-list", "/api/bans/", "Нет активных банов", showLoading, forceRender, true, "unban");
}

function scheduleLogSearch() {
    clearTimeout(logSearchTimer);
    logSearchTimer = setTimeout(() => loadLogs(true), 220);
}

function resetLogFilters() {
    if (qs("logs-search")) qs("logs-search").value = "";
    if (qs("logs-sort")) qs("logs-sort").value = "desc";
    if (qs("logs-category")) qs("logs-category").value = "";
    if (qs("logs-level")) qs("logs-level").value = "";
    if (qs("logs-source")) qs("logs-source").value = "";
    if (qs("logs-date-from")) qs("logs-date-from").value = "";
    if (qs("logs-date-to")) qs("logs-date-to").value = "";
    if (qs("logs-limit")) qs("logs-limit").value = "200";
    loadLogs(true, true);
}

function getLogFilters() {
    return {
        search: qs("logs-search")?.value?.trim() || "",
        sort: qs("logs-sort")?.value || "desc",
        category: qs("logs-category")?.value || "",
        level: qs("logs-level")?.value || "",
        source: qs("logs-source")?.value || "",
        date_from: qs("logs-date-from")?.value || "",
        date_to: qs("logs-date-to")?.value || "",
        limit: qs("logs-limit")?.value || "200"
    };
}

function buildQueryString(params) {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
        if (value !== undefined && value !== null && value !== "") query.set(key, value);
    });
    return query.toString();
}

function populateFilterSelect(selectId, values, placeholder) {
    const select = qs(selectId);
    if (!select) return;
    const previous = select.value;
    select.innerHTML = "";
    select.appendChild(new Option(placeholder, ""));
    values.forEach(value => select.appendChild(new Option(humanizeLogFilterValue(value), value)));
    if (previous && values.includes(previous)) select.value = previous;
}

function humanizeLogFilterValue(value) {
    const map = {
        security: "Защита",
        moderation: "Модерация",
        access: "Доступы",
        blacklist: "BlackList",
        protection: "Защита ролей",
        roles: "Роли",
        backup: "Бэкапы",
        system: "Система",
        bot: "Бот",
        discord: "Discord",
        panel: "Web panel",
        info: "Инфо",
        success: "Успех",
        warning: "Предупреждение",
        danger: "Критично"
    };
    return map[value] || value;
}

function renderLogPresets(filters) {
    const container = qs("logs-presets");
    if (!container) return;
    container.innerHTML = "";

    LOG_PRESETS.forEach(preset => {
        const button = el("button", "chip-btn", preset.label);
        button.type = "button";
        const isActive = (filters.category || "") === preset.category
            && (filters.level || "") === preset.level
            && (filters.source || "") === preset.source;
        if (isActive) button.classList.add("active");
        button.addEventListener("click", () => {
            qs("logs-category").value = preset.category;
            qs("logs-level").value = preset.level;
            qs("logs-source").value = preset.source;
            loadLogs(true, true);
        });
        container.appendChild(button);
    });
}

function renderLogSummary(summary) {
    const container = qs("logs-summary");
    if (!container) return;
    container.innerHTML = "";

    const cards = [
        ["Найдено", formatNumber(summary.total || 0), "Записей по текущим фильтрам"],
        ["Сегодня", formatNumber(summary.today_count || 0), "Событий с начала дня"],
        ["Критичные", formatNumber(summary.danger_count || 0), "В текущей выборке"],
        ["Последнее", summary.last_entry?.created_at_display || "-", summary.last_entry?.title || "Нет записей"]
    ];

    cards.forEach(([label, value, hint]) => {
        const card = el("div", "log-summary-card");
        card.append(
            el("div", "log-summary-label", label),
            el("div", "log-summary-value", value),
            el("div", "log-summary-hint", hint)
        );
        container.appendChild(card);
    });
}

function renderLogAlert(summary) {
    const container = qs("logs-alert");
    if (!container) return;
    container.innerHTML = "";

    const box = el("div", `log-alert${summary.danger_last_day > 0 ? " danger" : ""}`);
    if (summary.danger_last_day > 0) {
        box.textContent = `Мой быстрый обзор: за последние 24 часа зафиксировано ${summary.danger_last_day} критичных события. Это удобно, чтобы сразу видеть всплески риска.`;
    } else {
        box.textContent = "Мой быстрый обзор: за последние 24 часа критичных событий не было.";
    }
    container.appendChild(box);
}

function createLogBadge(text, className) {
    return el("span", `log-badge ${className}`, text);
}

function createLogCard(log) {
    const card = el("div", `log-card level-${log.level || "info"}`);

    const meta = el("div", "log-meta");
    meta.append(
        createLogBadge(humanizeLogFilterValue(log.category || "system"), "level-info"),
        createLogBadge(humanizeLogFilterValue(log.source || "bot"), "level-info"),
        createLogBadge(humanizeLogFilterValue(log.level || "info"), `level-${log.level || "info"}`)
    );

    const titleRow = el("div", "log-title-row");
    titleRow.append(
        el("h3", "log-title", log.title || "Событие"),
        el("div", "log-time", log.created_at_display || log.created_at || "-")
    );

    const description = el("div", "log-description", log.description || "");

    card.append(meta, titleRow, description);
    return card;
}

function renderLogList(logs) {
    const container = qs("logs-list");
    if (!container) return;
    container.innerHTML = "";

    if (!logs.length) {
        setState(container, "empty", "По текущим фильтрам логов не найдено");
        return;
    }

    const list = el("div", "log-stream");
    logs.forEach(log => list.appendChild(createLogCard(log)));
    container.appendChild(list);
}

async function loadLogs(showLoading = true, forceRender = false) {
    const guildId = qs("logs-guild")?.value;
    const container = qs("logs-list");
    if (!guildId) {
        setState(container, "empty", "Выберите сервер");
        if (qs("logs-summary")) qs("logs-summary").innerHTML = "";
        if (qs("logs-alert")) qs("logs-alert").innerHTML = "";
        if (qs("logs-presets")) qs("logs-presets").innerHTML = "";
        updateMetrics();
        return;
    }

    const filters = getLogFilters();
    const query = buildQueryString(filters);
    if (showLoading) setState(container, "loading", "Загрузка логов...");

    try {
        const data = await fetchJson(`/api/logs/${guildId}?${query}`);
        populateFilterSelect("logs-category", data.filters?.categories || [], "Все категории");
        populateFilterSelect("logs-level", data.filters?.levels || [], "Все уровни");
        populateFilterSelect("logs-source", data.filters?.sources || [], "Все источники");
        renderLogPresets(filters);
        renderLogSummary(data.summary || {});
        renderLogAlert(data.summary || {});

        const payload = {
            guildId,
            filters,
            logs: data.logs || [],
            summary: data.summary || {}
        };

        if (changed("logs", payload, forceRender || showLoading)) {
            renderLogList(data.logs || []);
        }
        updateMetrics();
        markUpdated();
    } catch (error) {
        console.error("Logs error:", error);
        if (showLoading || !container.children.length) setState(container, "empty", "Ошибка загрузки логов");
    }
}

async function loadUserList(key, selectId, containerId, url, emptyText, showLoading = true, forceRender = false, showBan = false, removeAction = null) {
    const guildId = qs(selectId)?.value;
    const container = qs(containerId);
    if (!guildId) {
        setState(container, "empty", "Выберите сервер");
        updateMetrics();
        return;
    }
    if (showLoading) setState(container, "loading", "Загрузка...");

    try {
        const users = await fetchJson(url + guildId);
        const data = { guildId, users };
        if (!Array.isArray(users) || !users.length) {
            if (changed(key, data, forceRender || showLoading) || !container.children.length) setState(container, "empty", emptyText);
            updateMetrics();
            markUpdated();
            return;
        }
        if (changed(key, data, forceRender || showLoading)) {
            renderUserList(container, users, showBan, removeAction, guildId);
        }
        updateMetrics();
        markUpdated();
    } catch (error) {
        console.error("User list error:", error);
        if (showLoading || !container.children.length) setState(container, "empty", "Ошибка загрузки");
    }
}

function renderUserList(container, users, showBan, removeAction, guildId) {
    container.innerHTML = "";
    const list = el("div", "member-list");
    users.forEach(user => list.appendChild(createUserCard(user, showBan ? user.type : null, showBan ? user.expire : null, removeAction, guildId)));
    container.appendChild(list);
}

function createUserCard(user, badgeText, expire, removeAction = null, guildId = null) {
    const card = el("div", "member-card");
    const header = el("div", "member-header");
    const avatar = el("img", "member-avatar");
    avatar.src = user.avatar || fallbackAvatar;
    avatar.alt = user.username || "avatar";
    avatar.loading = "lazy";

    const info = el("div", "member-info");
    info.append(el("div", "member-name", user.username || `User ${user.user_id}`), el("div", "member-id", user.user_id));
    if (expire) info.appendChild(el("div", "small-note", `Истекает: ${expire}`));
    if (user.issued_by) info.appendChild(el("div", "small-note", `Выдал: ${user.issued_by}`));
    if (badgeText) info.appendChild(badge(badgeText, "status-ban"));
    if (removeAction && guildId) {
        const actions = el("div", "actions-container");
        const text = removeAction === "unban" ? "Разбанить" : "Снять";
        actions.appendChild(actionButton(removeAction, text, "btn-remove", guildId, user.user_id));
        info.appendChild(actions);
    }

    header.append(avatar, info);
    card.appendChild(header);
    return card;
}

function invalidateActionCaches() {
    ["members", "whitelist", "blacklist", "protected", "bans", "logs"].forEach(key => {
        signatures[key] = "";
    });
}

async function refreshAfterAction() {
    if (activeTab === "manage") return loadMembers(false, true);
    if (activeTab === "whitelist") return loadWhitelist(false, true);
    if (activeTab === "blacklist") return loadBlacklist(false, true);
    if (activeTab === "protected") return loadProtected(false, true);
    if (activeTab === "bans") return loadBans(false, true);
    if (activeTab === "logs") return loadLogs(false, true);
    return refreshActiveTab(false);
}

function copyToClipboard(text) {
    if (!navigator.clipboard) {
        showNotification("Буфер обмена недоступен", "error");
        return;
    }
    navigator.clipboard.writeText(text).then(showCopyNotification).catch(() => {
        showNotification("Не удалось скопировать", "error");
    });
}

function showCopyNotification() {
    const notification = qs("copy-notification");
    notification.classList.add("show");
    setTimeout(() => notification.classList.remove("show"), 1600);
}

function closeGuildModal() {
    qs("guild-modal")?.classList.remove("active");
}

document.addEventListener("keydown", event => {
    if (event.key === "Escape") closeGuildModal();
});

document.addEventListener("click", event => {
    const modal = qs("guild-modal");
    if (event.target === modal) closeGuildModal();
});

function showNotification(message, type) {
    const notification = el("div", `notification ${type}`, message);
    document.body.appendChild(notification);
    setTimeout(() => notification.classList.add("show"), 10);
    setTimeout(() => {
        notification.classList.remove("show");
        setTimeout(() => notification.remove(), 220);
    }, 2800);
}
