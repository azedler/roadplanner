import { actionButton } from "../lib/action-button.js";
import { escapeHtml, cleanText } from "../lib/core-helpers.js";
import { WS_ACTION } from "../lib/constants.js";

export const mediaMixin = {
  async _maybeAutoPopulateDestinationGalleries(payload) {
    const tripId = cleanText(payload?.selected_trip_id);
    const revision = Number(payload?.summary?.revision ?? 0);
    if (!tripId || !this._canEdit() || !payload?.settings?.destination_image_auto_fill) return;
    const key = `${tripId}:${revision}`;
    if (this._destinationAutoFillRequested.has(key) || this._destinationAutoFillInFlight) return;
    if (payload?.experience?.destination_enrichment?.state === "running") return;
    const stops = (payload?.days?.days || []).flatMap((day) => this._dayRoadbookStops(day));
    if (!stops.length) return;
    const galleries = payload?.experience?.destination_galleries || {};
    const ownByStop = payload?.experience?.by_stop || {};
    const missing = stops.some((stop) => stop?.id
      && !(Array.isArray(ownByStop?.[stop.id]) && ownByStop[stop.id].length)
      && !galleries[stop.id]);
    if (!missing) {
      this._destinationAutoFillRequested.add(key);
      return;
    }
    this._destinationAutoFillRequested.add(key);
    this._destinationAutoFillInFlight = true;
    try {
      // The backend now performs bounded background enrichment. The panel only
      // starts one small best-effort batch so the first visible stops do not
      // wait for the next scheduler interval.
      const result = await this._send({
        type: WS_ACTION,
        action: "auto_populate_destination_galleries",
        data: { trip_id: tripId, limit: 4 },
      });
      if (result?.experience && this._data?.selected_trip_id === tripId) {
        this._data = { ...this._data, experience: result.experience };
        this._signature = "";
        this._render({ preserveScroll: true });
      }
    } catch (error) {
      console.warn("Roadplanner automatic destination image search failed", error);
      this._destinationAutoFillRequested.delete(key);
    } finally {
      this._destinationAutoFillInFlight = false;
    }
  },

  async _searchImages(context) {
    if (this._busy) return;
    this._setBusy(true);
    try {
      const stop = context?.targetType === "stop"
        ? this._findStop(context.dayId, context.stopId)
        : null;
      // Pitch options are not stops; their coordinates arrive via context.
      const location = context?.location || stop?.location || {};
      const result = await this._send({
        type: WS_ACTION,
        action: "search_destination_images",
        data: {
          query: context.query,
          limit: 12,
          latitude: location.latitude ?? location.lat ?? null,
          longitude: location.longitude ?? location.lon ?? location.lng ?? null,
        },
      });
      const existingGallery = context?.targetType === "stop"
        ? this._destinationGalleryForStop(context.stopId)
        : null;
      const existing = this._destinationGalleryImages(existingGallery);
      const combined = [];
      const seen = new Set();
      for (const image of [...existing, ...(result.results || [])]) {
        const identity = cleanText(image?.id || image?.source_url || image?.image_url);
        if (!identity || seen.has(identity)) continue;
        seen.add(identity);
        combined.push(image);
      }
      const selectedIds = context?.targetType === "stop"
        ? existing.map((image) => cleanText(image.id || image.source_url || image.image_url)).filter(Boolean)
        : [];
      this._dialog = {
        type: "image-search",
        context,
        query: result.query,
        results: combined,
        providerErrors: result.provider_errors || {},
        selectedIds: selectedIds.slice(0, 3),
        primaryImageId: cleanText(existingGallery?.primary_image_id || selectedIds[0]),
      };
      this._render({ preserveScroll: true });
    } catch (error) {
      this._showActionError(error, {
        title: "Bildsuche fehlgeschlagen",
        retry: () => this._searchImages(context),
      });
    } finally {
      this._setBusy(false);
    }
  },

  _imageSearchIdentity(image) {
    return cleanText(image?.id || image?.source_url || image?.image_url);
  },

  _toggleImageSearchResult(index) {
    if (this._dialog?.type !== "image-search" || this._dialog?.context?.targetType !== "stop") return;
    const image = this._dialog.results?.[index];
    const identity = this._imageSearchIdentity(image);
    if (!identity) return;
    const selected = new Set(this._dialog.selectedIds || []);
    if (selected.has(identity)) {
      selected.delete(identity);
      if (this._dialog.primaryImageId === identity) {
        this._dialog.primaryImageId = [...selected][0] || "";
      }
    } else {
      if (selected.size >= 3) {
        this._showToast("Pro Stopp können höchstens drei Planungsbilder aktiv sein.", "error", 5000);
        return;
      }
      selected.add(identity);
      if (!this._dialog.primaryImageId) this._dialog.primaryImageId = identity;
    }
    this._dialog.selectedIds = [...selected];
    this._render({ preserveScroll: true });
  },

  _setImageSearchPrimary(index) {
    if (this._dialog?.type !== "image-search" || this._dialog?.context?.targetType !== "stop") return;
    const image = this._dialog.results?.[index];
    const identity = this._imageSearchIdentity(image);
    if (!identity) return;
    const selected = new Set(this._dialog.selectedIds || []);
    if (!selected.has(identity)) {
      if (selected.size >= 3) selected.delete([...selected][selected.size - 1]);
      selected.add(identity);
    }
    this._dialog.selectedIds = [...selected];
    this._dialog.primaryImageId = identity;
    this._render({ preserveScroll: true });
  },

  async _saveImageSearchGallery() {
    const dialog = this._dialog;
    if (dialog?.type !== "image-search" || dialog?.context?.targetType !== "stop") return;
    const selected = new Set(dialog.selectedIds || []);
    const images = (dialog.results || []).filter((image) => selected.has(this._imageSearchIdentity(image))).slice(0, 3);
    if (!images.length) {
      this._showToast("Wähle mindestens ein Bild aus.", "error", 4500);
      return;
    }
    const primary = dialog.primaryImageId && selected.has(dialog.primaryImageId)
      ? dialog.primaryImageId
      : this._imageSearchIdentity(images[0]);
    const context = dialog.context;
    const result = await this._runAction("save_destination_gallery", {
      trip_id: this._selectedTripId,
      day_id: context.dayId,
      stop_id: context.stopId,
      images,
      primary_image_id: primary,
    }, "Bildergalerie gespeichert");
    if (result) this._closeDialog({ flushRefresh: false });
  },

  async _chooseImage(index) {
    const dialog = this._dialog;
    const image = dialog?.results?.[index];
    const context = dialog?.context;
    if (!image || !context) return;
    if (context.targetType === "stop") {
      this._setImageSearchPrimary(index);
      return;
    }
    this._closeDialog({ flushRefresh: false });
    const media = {
      image_url: image.image_url,
      alt: image.alt || image.title,
      attribution: image.attribution,
      source_url: image.source_url,
      provider: image.provider,
    };
    const day = this._findDay(context.dayId);
    await this._runAction("update_day", {
      day_id: context.dayId,
      patch: { details: this._detailsWithMedia(day, media) },
      expected_revision: this._currentRevision(),
    }, "Titelbild gespeichert");
  },

  async _refreshDestinationGallery(dayId, stopId) {
    if (!dayId || !stopId) return;
    const result = await this._runAction("refresh_destination_gallery", {
      trip_id: this._selectedTripId,
      day_id: dayId,
      stop_id: stopId,
    }, "Bilder aktualisiert", {
      errorMode: "dialog",
      errorTitle: "Bilder konnten nicht aktualisiert werden",
      retry: () => this._refreshDestinationGallery(dayId, stopId),
    });
    if (result?.gallery?.images?.length) {
      const resolvedDayId = cleanText(result.gallery.day_id) || dayId;
      const resolvedStopId = cleanText(result.gallery.stop_id) || stopId;
      this._dialog = {
        type: "destination-gallery",
        images: result.gallery.images,
        index: 0,
        title: this._findStop(resolvedDayId, resolvedStopId)?.name || "Stoppbilder",
        dayId: resolvedDayId,
        stopId: resolvedStopId,
        primaryImageId: result.gallery.primary_image_id,
        readOnly: !this._canEdit(),
      };
      this._render({ preserveScroll: true });
    }
  },

  async _updateDestinationGalleryFromDialog({ primaryOnly = false, removeCurrent = false, move = 0 } = {}) {
    const dialog = this._dialog;
    if (dialog?.type !== "destination-gallery" || dialog.readOnly || !dialog.stopId) return;
    const images = [...(dialog.images || [])];
    let index = Math.max(0, Math.min(images.length - 1, Number(dialog.index || 0)));
    if (!images.length) return;
    let primaryImageId = cleanText(dialog.primaryImageId || images[0]?.id);
    if (removeCurrent) {
      const [removed] = images.splice(index, 1);
      if (removed?.id === primaryImageId) primaryImageId = cleanText(images[0]?.id);
      index = Math.max(0, Math.min(images.length - 1, index));
    } else if (move) {
      const target = Math.max(0, Math.min(images.length - 1, index + move));
      if (target !== index) {
        [images[index], images[target]] = [images[target], images[index]];
        index = target;
      }
    } else if (primaryOnly) {
      primaryImageId = cleanText(images[index]?.id);
    }
    if (!images.length) {
      await this._runAction("delete_destination_gallery", {
        trip_id: this._selectedTripId,
        stop_id: dialog.stopId,
      }, "Bildergalerie entfernt");
      this._closeDialog({ flushRefresh: false });
      return;
    }
    const result = await this._runAction("save_destination_gallery", {
      trip_id: this._selectedTripId,
      day_id: dialog.dayId,
      stop_id: dialog.stopId,
      images,
      primary_image_id: primaryImageId,
    }, primaryOnly ? "Hauptbild gesetzt" : "Bildergalerie gespeichert");
    if (!result) return;
    dialog.images = result.gallery?.images || images;
    dialog.primaryImageId = result.gallery?.primary_image_id || primaryImageId;
    dialog.index = index;
    this._dialog = dialog;
    this._render({ preserveScroll: true });
  },

  async _removeImage(context) {
    if (context.targetType === "day") {
      const day = this._findDay(context.dayId);
      await this._runAction("update_day", {
        day_id: context.dayId,
        patch: { details: this._detailsWithMedia(day, null) },
        expected_revision: this._currentRevision(),
      }, "Titelbild entfernt");
    } else {
      const stop = this._findStop(context.dayId, context.stopId);
      await this._runAction("update_stop", {
        day_id: context.dayId,
        stop_id: context.stopId,
        patch: { details: this._detailsWithMedia(stop, null) },
        expected_revision: this._currentRevision(),
      }, "Zielbild entfernt");
    }
  },

  _destinationGalleries() {
    const value = this._experienceData().destination_galleries;
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  },

  _destinationGalleryForStop(stopId) {
    return stopId ? this._destinationGalleries()[stopId] || null : null;
  },

  _destinationGalleryImages(gallery) {
    return Array.isArray(gallery?.images)
      ? gallery.images.filter((image) => this._safeUrl(image?.image_url || image?.thumbnail_url)).slice(0, 3)
      : [];
  },

  _destinationGalleryPrimary(gallery) {
    const images = this._destinationGalleryImages(gallery);
    if (!images.length) return null;
    return images.find((image) => image.id === gallery?.primary_image_id) || images[0];
  },

  _destinationProviderLabel(provider) {
    const labels = {
      wikimedia_commons: "Wikimedia Commons",
      openverse: "Openverse",
      google_places: "Google",
      manual: "Manuell",
      onedrive: "OneDrive",
      shared_link: "Geteilter Link",
    };
    return labels[cleanText(provider)] || cleanText(provider) || "Bildquelle";
  },

  _renderDestinationGalleryPreview(gallery, { dayId = "", stopId = "", compact = false } = {}) {
    const images = this._destinationGalleryImages(gallery);
    if (!images.length) return "";
    const primary = this._destinationGalleryPrimary(gallery) || images[0];
    const ordered = [primary, ...images.filter((image) => image.id !== primary.id)].slice(0, 3);
    const curationLabel = gallery?.curation?.mode === "hybrid_vision" ? " · KI kuratiert" : " · lokal vorausgewählt";
    return `<div class="destination-gallery-preview ${compact ? "compact" : ""}">
      <button class="destination-gallery-main" type="button" data-action="destination-gallery-open" data-day-id="${escapeHtml(dayId)}" data-stop-id="${escapeHtml(stopId)}" data-image-index="0"><img data-destination-image loading="lazy" decoding="async" referrerpolicy="no-referrer" src="${escapeHtml(this._safeUrl(primary.thumbnail_url || primary.image_url))}" alt="${escapeHtml(primary.alt || primary.title || "Reiseziel")}"><span><ha-icon icon="mdi:image-multiple-outline"></ha-icon>Planungsbilder · ${images.length}${escapeHtml(curationLabel)}</span></button>
      ${ordered.length > 1 ? `<div class="destination-gallery-thumbs">${ordered.slice(1).map((image, index) => `<button type="button" data-action="destination-gallery-open" data-day-id="${escapeHtml(dayId)}" data-stop-id="${escapeHtml(stopId)}" data-image-index="${index + 1}"><img data-destination-image loading="lazy" decoding="async" referrerpolicy="no-referrer" src="${escapeHtml(this._safeUrl(image.thumbnail_url || image.image_url))}" alt="${escapeHtml(image.alt || image.title || "Reiseziel")}"></button>`).join("")}</div>` : ""}
    </div>`;
  },

  _renderDestinationGalleryStatus(gallery, dayId, stopId, hasOwnImages = false) {
    if (!gallery || this._destinationGalleryImages(gallery).length) return "";
    const errors = gallery.provider_errors && typeof gallery.provider_errors === "object"
      ? Object.values(gallery.provider_errors).filter(Boolean)
      : [];
    const failed = gallery.status === "error" || errors.length;
    // Own photos are the best possible state - a notice about missing
    // EXTERNAL planning images is pure noise then (user feedback).
    if (hasOwnImages) return "";
    return `<div class="destination-gallery-inline ${failed ? "warning" : "neutral"}"><ha-icon icon="${failed ? "mdi:image-off-outline" : "mdi:image-search-outline"}"></ha-icon><div><strong>${failed ? "Bilder konnten noch nicht geladen werden" : "Noch keine passenden Planungsbilder"}</strong><span>${failed ? "Andere Stoppinformationen bleiben vollständig verfügbar." : "Roadplanner sucht im Hintergrund nach passenden Bildern."}</span></div>${this._canEdit() ? `<button class="text-button" type="button" data-action="destination-gallery-refresh" data-day-id="${escapeHtml(dayId)}" data-stop-id="${escapeHtml(stopId)}">Erneut versuchen</button>` : ""}</div>`;
  },

  _stepDestinationGallery(delta) {
    if (this._dialog?.type !== "destination-gallery") return;
    const images = this._dialog.images || [];
    if (!images.length) return;
    this._dialog.index = (Number(this._dialog.index || 0) + delta + images.length) % images.length;
    this._render({ preserveScroll: true });
  },

  _experienceMediaByIds(ids) {
    const media = this._experienceData().media || [];
    const byId = new Map(media.filter((item) => item?.id).map((item) => [item.id, item]));
    return (Array.isArray(ids) ? ids : []).map((id) => byId.get(id)).filter(Boolean);
  },

  _experienceAllMediaForDay(dayId) {
    if (!dayId) return [];
    return this._experienceMediaByIds(this._experienceData().by_day?.[dayId] || []);
  },

  _experienceAllMediaForStop(stopId) {
    if (!stopId) return [];
    return this._experienceMediaByIds(this._experienceData().by_stop?.[stopId] || []);
  },

  _experienceMediaForDay(dayId) {
    if (!dayId) return [];
    const presentationIds = this._experiencePresentation().day_highlights?.[dayId];
    return Array.isArray(presentationIds) && presentationIds.length
      ? this._experienceMediaByIds(presentationIds)
      : this._experienceAllMediaForDay(dayId).slice(0, 3);
  },

  _experienceMediaForStop(stopId) {
    if (!stopId) return [];
    const presentationIds = this._experiencePresentation().stop_highlights?.[stopId];
    return Array.isArray(presentationIds) && presentationIds.length
      ? this._experienceMediaByIds(presentationIds)
      : this._experienceAllMediaForStop(stopId).slice(0, 3);
  },

  _renderMedia() {
    const experience = this._experienceData();
    const media = experience.media || [];
    const oneDrive = experience.onedrive || {};
    const vision = experience.vision || {};
    const syncState = oneDrive.sync_state || {};
    const scanStats = syncState.scan_stats || {};
    const statusText = oneDrive.connected ? `${oneDrive.account_name || "OneDrive"} verbunden` : oneDrive.configured ? "Bereit zur Microsoft-Anmeldung" : "Noch nicht eingerichtet";
    const mediaFilter = this._mediaFilter || "all";
    const matchesFilter = (item) => {
      if (mediaFilter === "unassigned") return (item.assignment_status || "unassigned") === "unassigned";
      if (mediaFilter === "suggested") return item.assignment_status === "suggested";
      if (mediaFilter === "assigned") return ["automatic", "manual"].includes(item.assignment_status);
      return true;
    };
    // Keep the ABSOLUTE index into experience.media - media-open and the
    // detail dialog navigate the full list by that index.
    const filtered = media
      .map((item, absoluteIndex) => ({ item, absoluteIndex }))
      .filter(({ item }) => matchesFilter(item));
    const pageSize = 60;
    const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
    const page = Math.max(0, Math.min(Number(this._mediaPage || 0), pageCount - 1));
    const latest = filtered.slice(page * pageSize, (page + 1) * pageSize);
    const filterChip = (value, label, count) => `<button class="text-button media-filter-chip ${mediaFilter === value ? "active" : ""}" type="button" data-action="media-filter" data-filter="${value}">${label}${Number.isFinite(count) ? ` (${count})` : ""}</button>`;
    const counts = {
      all: media.length,
      unassigned: media.filter((item) => (item.assignment_status || "unassigned") === "unassigned").length,
      suggested: media.filter((item) => item.assignment_status === "suggested").length,
      assigned: media.filter((item) => ["automatic", "manual"].includes(item.assignment_status)).length,
    };
    const mediaControls = `<div class="media-controls"><div class="media-filter-row">${filterChip("all", "Alle", counts.all)}${filterChip("assigned", "Zugeordnet", counts.assigned)}${filterChip("suggested", "Zu prüfen", counts.suggested)}${filterChip("unassigned", "Ohne Tag", counts.unassigned)}</div>${pageCount > 1 ? `<div class="media-page-row"><button class="text-button" type="button" data-action="media-page" data-delta="-1" ${page <= 0 ? "disabled" : ""}><ha-icon icon="mdi:chevron-left"></ha-icon>Neuere</button><span>Bilder ${filtered.length ? page * pageSize + 1 : 0}–${Math.min((page + 1) * pageSize, filtered.length)} von ${filtered.length}</span><button class="text-button" type="button" data-action="media-page" data-delta="1" ${page >= pageCount - 1 ? "disabled" : ""}>Ältere<ha-icon icon="mdi:chevron-right"></ha-icon></button></div>` : ""}</div>`;
    const phase = syncState.mode || "";
    const phaseLabel = { initial_scan: "Selektiver Erstscan", delta_catchup: "Änderungen seit Scan nachziehen", delta: "Nur Änderungen" }[phase] || "Synchronisierung";
    const currentFolderValue = String(scanStats.current_folder || "");
    const currentFolder = currentFolderValue
      ? `<span class="onedrive-current-folder" title="${escapeHtml(currentFolderValue)}"><b>Aktueller Ordner:</b> ${escapeHtml(currentFolderValue)}</span>`
      : "";
    const relevantFolders = Number(scanStats.folders_discovered || 0);
    const completedFolders = Number(scanStats.folders_completed || 0);
    const skippedFolders = Number(scanStats.folders_skipped || 0);
    const progressSummary = `${Number(scanStats.entries_examined || 0).toLocaleString("de-DE")} Einträge geprüft · ${completedFolders.toLocaleString("de-DE")}/${relevantFolders.toLocaleString("de-DE")} relevante Ordner abgeschlossen · ${skippedFolders.toLocaleString("de-DE")} historische/technische Ordner übersprungen · ${Number(scanStats.relevant_photos || 0).toLocaleString("de-DE")} Reisefotos gefunden`;
    const syncNotice = syncState.truncated
      ? `<div class="notice neutral onedrive-sync-notice"><ha-icon icon="mdi:folder-sync-outline"></ha-icon><div><strong>${escapeHtml(phaseLabel)} läuft</strong><span>${progressSummary}.</span>${currentFolder}<small>Pro Lauf werden bis zu ${Number(oneDrive.max_items_per_run || 2000).toLocaleString("de-DE")} Metadateneinträge beziehungsweise höchstens ${Number(oneDrive.max_scan_seconds || 12).toLocaleString("de-DE")} Sekunden verarbeitet. Tippe erneut auf „Jetzt synchronisieren“ oder warte auf den nächsten automatischen Lauf.</small></div></div>`
      : syncState.last_sync_at
        ? `<div class="notice neutral"><ha-icon icon="mdi:clock-check-outline"></ha-icon><div><strong>OneDrive-Delta aktiv</strong><span>Zuletzt synchronisiert: ${escapeHtml(this._formatTimestamp(syncState.last_sync_at))} · Reisezeitraum ${escapeHtml(syncState.trip_date_range || "")} · historische Bilder außerhalb des Zeitfensters werden nicht übernommen.</span></div></div>`
        : "";
    const visionUsed = Number(vision.usage?.count || 0);
    const visionLimit = Number(vision.daily_limit || 0);
    const visionLabel = vision.enabled
      ? `Hybrid · lokal vorgefiltert · Gemini Vision${visionLimit ? ` · ${visionUsed}/${visionLimit} heute` : ""}`
      : "Lokal · keine Bilder werden an Gemini gesendet";
    const visionNotice = `<div class="notice neutral"><ha-icon icon="${vision.enabled ? "mdi:creation-outline" : "mdi:shield-lock-outline"}"></ha-icon><div><strong>Smart-Media-Auswahl</strong><span>${escapeHtml(visionLabel)}</span>${vision.enabled ? `<small>Roadplanner sendet höchstens ${Number(vision.max_candidates || 12)} lokal ausgewählte Vorschaubilder je Stopp. Unveränderte Kandidatensätze werden nicht erneut analysiert.</small>` : `<small>Aktiviere den Hybrid-Modus bewusst in den Integrationsoptionen, um nach der lokalen Vorauswahl eine semantische Best-of-Auswahl zu verwenden.</small>`}</div></div>`;
    return `
      ${this._renderReadOnlyNotice()}
      <section class="panel-card media-toolbar">
        <div><span class="eyebrow">OneDrive Personal</span><h2>Reisefotos automatisch zuordnen</h2><p>Roadplanner liest den Ordner <strong>${escapeHtml(oneDrive.folder_path || "Pictures/Camera Roll")}</strong>${oneDrive.recursive_subfolders ? " einschließlich Unterordnern" : ""}, berücksichtigt nur den Zeitraum der ausgewählten Reise mit ${Number(oneDrive.date_buffer_days || 0)} Tagen Puffer und kopiert keine Originale nach Home Assistant.</p></div>
        <div class="media-toolbar-actions"><span class="assistant-health ${oneDrive.connected ? "success" : "muted"}"><ha-icon icon="mdi:microsoft-onedrive"></ha-icon>${escapeHtml(statusText)}</span>${oneDrive.connected ? `${actionButton(this._actionCosts(), "onedrive-sync", "Neue Fotos holen")}<button class="text-button" type="button" data-action="onedrive-full-sync" title="Liest den ganzen Reisezeitraum noch einmal ein, mit dem aktuell eingestellten Puffer."><ha-icon icon="mdi:calendar-refresh-outline"></ha-icon>Alles neu einlesen</button><button class="text-button" type="button" data-action="onedrive-setup"><ha-icon icon="mdi:cog-outline"></ha-icon>Einrichtung</button><button class="text-button danger-text" type="button" data-action="onedrive-disconnect">Trennen</button>` : oneDrive.configured ? `<button class="primary-button" type="button" data-action="onedrive-connect"><ha-icon icon="mdi:login-variant"></ha-icon>Mit Microsoft anmelden</button><button class="text-button" type="button" data-action="onedrive-setup"><ha-icon icon="mdi:cog-outline"></ha-icon>Einrichtung ändern</button>` : `<button class="primary-button" type="button" data-action="onedrive-setup"><ha-icon icon="mdi:microsoft-onedrive"></ha-icon>OneDrive einrichten</button>`}${vision.enabled && this._canEdit() ? actionButton(this._actionCosts(), "media-curate-trip", "Fotos neu bewerten lassen", { extra: 'data-limit="5"' }) : ""}${this._canEdit() ? actionButton(this._actionCosts(), "media-reassign", "Zuordnungen neu berechnen") : ""}</div>
      </section>
      ${syncNotice}
      ${visionNotice}
      <section class="media-stat-grid">
        ${this._mediaStat("mdi:image-multiple-outline", "Fotos", experience.stats?.media_count || 0)}
        ${this._mediaStat("mdi:check-decagram-outline", "Automatisch", experience.stats?.automatic_count || 0)}
        ${this._mediaStat("mdi:star-four-points-outline", "Stopp-Highlights", experience.stats?.featured_stop_count || 0)}
        ${this._mediaStat("mdi:image-filter-center-focus-strong-outline", "Zurückgestellt", Number(experience.stats?.media_duplicate_count || 0) + Number(experience.stats?.media_burst_suppressed_count || 0))}
        ${this._mediaStat("mdi:help-circle-outline", "Zu prüfen", experience.stats?.suggested_count || 0)}
        ${this._mediaStat("mdi:image-off-outline", "Ohne Tag", experience.stats?.unassigned_count || 0)}
      </section>
      ${media.length ? mediaControls : ""}
      ${latest.length ? `<section class="media-grid">${latest.map(({ item, absoluteIndex }) => this._renderMediaCard(item, absoluteIndex)).join("")}</section>` : media.length ? `<div class="empty-state compact-empty"><ha-icon icon="mdi:image-filter-none"></ha-icon><h2>Keine Bilder in dieser Auswahl</h2><p>Wähle oben einen anderen Filter.</p></div>` : `<div class="empty-state"><ha-icon icon="mdi:image-multiple-outline"></ha-icon><h2>Noch keine OneDrive-Fotos</h2><p>Verbinde OneDrive Personal und starte anschließend eine Synchronisierung. Bereits vorhandene Fotos im gewählten Kameraordner werden anhand von Datum und GPS zugeordnet.</p></div>`}
    `;
  },

  /**
   * Re-decide the stored assignments, and say what changed.
   *
   * "Fertig" would be useless here: the whole point is the number. If a
   * rule change turns 253 photographs waiting for a click into twelve,
   * that is the message; and if it changes nothing, that is worth
   * knowing too, because it means the doubt was real.
   */
  async _mediaReassign() {
    const result = await this._runAction(
      "media_reassign",
      { trip_id: this._selectedTripId },
      "",
      {
        refresh: false,
        errorMode: "dialog",
        errorTitle: "Die Zuordnungen konnten nicht neu berechnet werden",
      },
    );
    if (!result) return;
    const summary = result.media_reassign || {};
    const changed = Number(summary.changed || 0);
    const automatic = Number(summary.became_automatic || 0);
    if (!changed) {
      this._showToast("Keine Zuordnung hat sich geändert.", "success", 4000);
    } else {
      const detail = automatic
        ? `${automatic} davon brauchen keine Prüfung mehr`
        : "keine davon wurde automatisch";
      this._showToast(`${changed} Zuordnungen neu berechnet – ${detail}.`, "success", 6000);
    }
    await this._loadData({ silent: true, force: true });
  },

  _mediaStat(icon, label, value) {
    return `<article class="panel-card media-stat"><ha-icon icon="${icon}"></ha-icon><div><strong>${Number(value).toLocaleString("de-DE")}</strong><span>${escapeHtml(label)}</span></div></article>`;
  },

  _renderMediaCard(item, index) {
    const day = item.linked_day_id ? this._findDay(item.linked_day_id) : null;
    const stop = item.linked_stop_id && item.linked_day_id ? this._findStop(item.linked_day_id, item.linked_stop_id) : null;
    const assignment = { automatic: "Automatisch", suggested: "Zu prüfen", manual: "Manuell", unassigned: "Nicht zugeordnet" }[item.assignment_status] || "Nicht zugeordnet";
    const statusClass = item.assignment_status === "automatic" || item.assignment_status === "manual" ? "status-success" : "status-warning";
    return `<article class="media-card ${item.is_cover ? "cover" : ""}">
      <button type="button" class="media-thumb" data-action="media-open" data-media-index="${index}"><img src="${escapeHtml(this._safeUrl(item.thumbnail_url))}" alt="${escapeHtml(item.caption || item.name || "Reisefoto")}" loading="lazy">${item.is_cover ? `<span class="cover-badge"><ha-icon icon="mdi:star"></ha-icon>Titelbild</span>` : ""}</button>
      <div class="media-card-copy"><div class="media-card-title"><strong>${escapeHtml(item.caption || item.name || "Foto")}</strong><span class="status-badge ${statusClass}">${escapeHtml(assignment)}</span></div><span>${escapeHtml(item.taken_at ? this._formatTimestamp(item.taken_at) : "Aufnahmezeit unbekannt")}</span><small>${escapeHtml(stop?.name || day?.title || "Noch keinem Reisetag zugeordnet")}${Number.isFinite(Number(item.distance_m)) ? ` · ${Math.round(Number(item.distance_m))} m` : ""}</small></div>
      <div class="media-card-actions"><button class="icon-button" type="button" data-action="media-edit" data-media-id="${escapeHtml(item.id)}" title="Zuordnung bearbeiten"><ha-icon icon="mdi:pencil-outline"></ha-icon></button>${item.linked_stop_id && !item.is_cover ? `<button class="icon-button" type="button" data-action="media-cover" data-media-id="${escapeHtml(item.id)}" title="Als Titelbild"><ha-icon icon="mdi:star-outline"></ha-icon></button>` : ""}<a class="icon-button" href="${escapeHtml(this._safeUrl(item.original_url))}" target="_blank" rel="noopener noreferrer" title="Original öffnen"><ha-icon icon="mdi:open-in-new"></ha-icon></a></div>
    </article>`;
  },

  _renderImageGallery(images) {
    return `<div class="image-gallery">${images.map((image) => `<figure class="gallery-item">${this._renderDestinationImage(image, { compact: false })}<figcaption><strong>${escapeHtml(image.context || image.alt)}</strong>${image.attribution ? `<span>${image.source_url ? `<a href="${escapeHtml(image.source_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(image.attribution)}</a>` : escapeHtml(image.attribution)}</span>` : ""}</figcaption></figure>`).join("")}</div>`;
  },

  _renderDestinationImage(image, { compact = false } = {}) {
    const url = this._safeUrl(image?.image_url);
    if (!url) return "";
    return `<div class="destination-image ${compact ? "compact" : ""}"><img data-destination-image loading="lazy" decoding="async" referrerpolicy="no-referrer" src="${escapeHtml(url)}" alt="${escapeHtml(image.alt || image.context || "Reiseziel")}"><div class="image-fallback"><ha-icon icon="mdi:image-off-outline"></ha-icon><span>Bild nicht verfügbar</span></div></div>`;
  },

  _renderOneDriveSetup(_dialog) {
    const oneDrive = this._experienceData().onedrive || {};
    const setup = oneDrive.setup_settings || {};
    const folderPath = setup.folder_path || oneDrive.folder_path || "Pictures/Camera Roll";
    const interval = Number(setup.sync_interval_minutes || oneDrive.sync_interval_minutes || 15);
    const recursive = setup.recursive_subfolders ?? oneDrive.recursive_subfolders ?? true;
    const dateBufferDays = Number(setup.date_buffer_days ?? oneDrive.date_buffer_days ?? 3);
    const maxItemsPerRun = Number(setup.max_items_per_run ?? oneDrive.max_items_per_run ?? 2000);
    const maxScanSeconds = Number(setup.max_scan_seconds ?? oneDrive.max_scan_seconds ?? 12);
    const configuredHint = oneDrive.client_id_hint || "";
    return `${this._renderModalHeader("OneDrive Personal einrichten", "Einmalige Microsoft-Appregistrierung, danach Anmeldung per Gerätecode")}
      <form data-form="onedrive-setup" class="form-grid onedrive-setup-form">
        <div class="notice info full"><ha-icon icon="mdi:information-outline"></ha-icon><div><strong>Kein API-Schlüssel und kein Client-Secret</strong><span>Microsoft verlangt für den Gerätecodefluss lediglich eine öffentliche Application (client) ID. Roadplanner speichert kein Microsoft-Passwort und benötigt nur lesenden Dateizugriff.</span></div></div>
        <div class="form-section full"><h3>1. Microsoft-App einmalig anlegen</h3><ol class="setup-steps"><li>Öffne Microsoft Entra und erstelle eine neue App-Registrierung.</li><li>Kontotyp: persönliche Microsoft-Konten oder Organisations- und persönliche Konten.</li><li>Unter „Authentifizierung“ öffentliche Clientflows erlauben.</li><li>Delegierte Graph-Berechtigungen: <code>Files.Read</code> und <code>User.Read</code>. <code>offline_access</code> wird bei der Anmeldung angefordert.</li><li>Kopiere die Application (client) ID hierher. Kein Geheimnis erstellen.</li></ol><a class="secondary-button inline-link-button" href="https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade" target="_blank" rel="noopener noreferrer"><ha-icon icon="mdi:open-in-new"></ha-icon>Microsoft App-Registrierungen öffnen</a></div>
        ${this._field("client_id", `Application (client) ID${configuredHint ? ` · bestehend ${configuredHint} · leer lassen zum Behalten` : ""}`, "", "text", !oneDrive.configured, "full")}
        <div class="form-section full"><h3>2. Fotoordner und Automatik</h3><p>Diese Einrichtung ist die einzige maßgebliche OneDrive-Konfiguration. Der Pfad ist relativ zum OneDrive-Stamm, zum Beispiel „Bilder/Eigene Aufnahmen“.</p></div>
        ${this._field("folder_path", "Kameraordner", folderPath, "text", true, "full")}
        ${this._archiveCheckbox("recursive_subfolders", "Unterordner rekursiv durchsuchen", recursive, "Jahres- und Monatsordner werden berücksichtigt. Ordner außerhalb des Reisezeitraums sowie versteckte Vorschaubildordner werden übersprungen.", "full")}
        ${this._field("date_buffer_days", "Puffer vor und nach der Reise (Tage)", dateBufferDays, "number", true, "", "0", "1")}
        ${this._field("max_items_per_run", "Maximale OneDrive-Einträge je Lauf", maxItemsPerRun, "number", true, "", "100", "100")}
        ${this._field("max_scan_seconds", "Maximale Laufzeit je Scan (Sekunden)", maxScanSeconds, "number", true, "", "3", "1")}
        ${this._field("sync_interval_minutes", "Synchronisierungsintervall (Minuten)", interval, "number", true, "", "5", "1")}
        ${this._archiveCheckbox("auto_sync", "Automatisch synchronisieren", setup.auto_sync ?? oneDrive.auto_sync ?? true, "Im Hintergrund wird nur die aktive Reise fortgesetzt. Große Erstscans laufen in begrenzten Paketen.", "full")}
        ${this._archiveCheckbox("auto_assign", "Automatisch nach Datum und GPS zuordnen", setup.auto_assign ?? oneDrive.auto_assign ?? true, "Eindeutige Treffer werden einem Tag oder Stopp zugeordnet; unsichere Treffer bleiben zur Prüfung.", "full")}
        <div class="notice neutral full"><ha-icon icon="mdi:shield-search-outline"></ha-icon><div><strong>Keine historischen Bilder werden heruntergeladen</strong><span>Der Erstscan liest nur OneDrive-Metadaten. Fotos außerhalb des Reisezeitraums werden nicht als Roadplanner-Medien übernommen. Nach Abschluss werden nur noch Änderungen über den Microsoft-Delta-Cursor gelesen.</span></div></div>
        <div class="modal-actions full"><button class="secondary-button" type="button" data-action="close-dialog">Abbrechen</button><button class="primary-button" type="submit"><ha-icon icon="mdi:microsoft-onedrive"></ha-icon>Speichern und verbinden</button></div>
      </form>`;
  },

  _renderOneDriveAuth(dialog) {
    const auth = dialog.auth || {};
    const verification = this._safeUrl(auth.verification_uri);
    return `${this._renderModalHeader("OneDrive Personal verbinden", "Microsoft Geräteanmeldung")}
      <div class="onedrive-auth-body"><ha-icon icon="mdi:microsoft-onedrive"></ha-icon><p>Öffne die Microsoft-Anmeldeseite, gib den Code ein und erlaube Roadplanner ausschließlich lesenden Zugriff auf deine OneDrive-Dateien.</p><div class="device-code">${escapeHtml(auth.user_code || "—")}</div>${verification ? `<a class="primary-button" href="${escapeHtml(verification)}" target="_blank" rel="noopener noreferrer"><ha-icon icon="mdi:open-in-new"></ha-icon>Microsoft-Anmeldung öffnen</a>` : ""}<small>${escapeHtml(auth.message || "Der Code ist zeitlich begrenzt.")}</small></div>
      <div class="modal-actions"><button class="secondary-button" type="button" data-action="close-dialog">Später</button><button class="primary-button" type="button" data-action="onedrive-poll"><ha-icon icon="mdi:check-network-outline"></ha-icon>Verbindung prüfen</button></div>`;
  },

  _renderMediaEdit(dialog) {
    const item = dialog.media || {};
    const days = this._data?.days?.days || [];
    const dayOptions = [{ value: "", label: "Nicht zugeordnet" }, ...days.map((day) => ({ value: day.id, label: `${this._formatDate(day.date)} · ${day.title || day.id}` }))];
    const stopOptions = [{ value: "", label: "Kein konkreter Stopp" }];
    for (const day of days) {
      for (const stop of this._canonicalStops(day.stops || [])) stopOptions.push({ value: `${day.id}::${stop.id}`, label: `${this._formatDate(day.date)} · ${stop.name}` });
    }
    const stopRef = item.linked_day_id && item.linked_stop_id ? `${item.linked_day_id}::${item.linked_stop_id}` : "";
    return `${this._renderModalHeader("Foto zuordnen", item.name || "OneDrive-Foto")}<form data-form="media-edit" data-media-id="${escapeHtml(item.id || "")}" class="form-grid">${this._archiveSelect("linked_day_id", "Reisetag", item.linked_day_id || "", dayOptions, "full")}${this._archiveSelect("linked_stop_ref", "Stopp", stopRef, stopOptions, "full")}${this._textarea("caption", "Bildunterschrift", item.caption || "", "full")}<label class="checkbox-field full"><input type="checkbox" name="is_cover" ${item.is_cover ? "checked" : ""}><span><strong>Als Titelbild dieses Stopps verwenden</strong><small>Roadplanner zeigt pro Stopp nur ein Titelbild.</small></span></label><label class="checkbox-field full"><input type="checkbox" name="is_day_cover" ${item.is_day_cover ? "checked" : ""}><span><strong>Als Titelbild dieses Reisetags verwenden</strong><small>Diese bewusste Auswahl hat Vorrang vor der automatischen Tagesauswahl.</small></span></label><label class="checkbox-field full"><input type="checkbox" name="is_trip_cover" ${item.is_trip_cover ? "checked" : ""}><span><strong>Als Titelbild der Reise verwenden</strong><small>Damit wird dieses Foto nicht mehr durch eine automatische Auswahl ersetzt.</small></span></label>${this._formActions("Zuordnung speichern")}</form><div class="modal-actions"><button class="text-button danger-text" type="button" data-action="media-delete" data-media-id="${escapeHtml(item.id || "")}">Aus Roadplanner entfernen</button></div>`;
  },

  _renderDestinationGallery(dialog) {
    const images = Array.isArray(dialog.images) ? dialog.images : [];
    if (!images.length) return `${this._renderModalHeader(dialog.title || "Bildergalerie")}<div class="empty-state">Keine Bilder</div>`;
    const index = Math.max(0, Math.min(images.length - 1, Number(dialog.index || 0)));
    const item = images[index];
    const isPrimary = cleanText(item.id) && cleanText(item.id) === cleanText(dialog.primaryImageId);
    const source = this._safeUrl(item.source_url || item.original_url);
    return `${this._renderModalHeader(dialog.title || "Bildergalerie", `${index + 1} von ${images.length}`)}
      <div class="media-gallery destination-gallery-dialog">
        <div class="media-gallery-stage" data-destination-gallery-stage><img data-destination-image src="${escapeHtml(this._safeUrl(item.original_url || item.image_url || item.thumbnail_url))}" alt="${escapeHtml(item.alt || item.title || "Reiseziel")}"></div>
        <div class="media-gallery-caption"><strong>${escapeHtml(item.title || item.alt || dialog.title || "Bild")}</strong><span>${escapeHtml(this._destinationProviderLabel(item.provider))}${item.license ? ` · ${escapeHtml(item.license)}` : ""}</span>${item.attribution ? `<small>${escapeHtml(item.attribution)}</small>` : ""}</div>
      </div>
      <div class="modal-actions media-gallery-actions"><button class="icon-button" type="button" data-action="destination-gallery-prev"><ha-icon icon="mdi:chevron-left"></ha-icon></button>${!dialog.readOnly ? `<button class="secondary-button" type="button" data-action="destination-gallery-move-left" ${index <= 0 ? "disabled" : ""}><ha-icon icon="mdi:arrow-left"></ha-icon>Nach links</button><button class="secondary-button" type="button" data-action="destination-gallery-primary" ${isPrimary ? "disabled" : ""}><ha-icon icon="mdi:star-outline"></ha-icon>${isPrimary ? "Hauptbild" : "Als Hauptbild"}</button><button class="secondary-button" type="button" data-action="destination-gallery-move-right" ${index >= images.length - 1 ? "disabled" : ""}>Nach rechts<ha-icon icon="mdi:arrow-right"></ha-icon></button><button class="text-button danger-text" type="button" data-action="destination-gallery-remove-image">Bild entfernen</button>` : ""}${source ? `<a class="secondary-button" href="${escapeHtml(source)}" target="_blank" rel="noopener noreferrer"><ha-icon icon="mdi:open-in-new"></ha-icon>Quelle</a>` : ""}<button class="icon-button" type="button" data-action="destination-gallery-next"><ha-icon icon="mdi:chevron-right"></ha-icon></button></div>`;
  },

  _renderMediaGallery(dialog) {
    const media = dialog.media || [];
    if (!media.length) return `${this._renderModalHeader("Fotoalbum")}<div class="empty-state">Keine Bilder</div>`;
    const index = Math.max(0, Math.min(media.length - 1, Number(dialog.index || 0)));
    const item = media[index];
    const day = item.linked_day_id ? this._findDay(item.linked_day_id) : null;
    const stop = item.linked_day_id && item.linked_stop_id ? this._findStop(item.linked_day_id, item.linked_stop_id) : null;
    return `${this._renderModalHeader("Fotoalbum", `${index + 1} von ${media.length}`)}<div class="media-gallery"><div class="media-gallery-stage"><img src="${escapeHtml(this._safeUrl(item.original_url || item.thumbnail_url))}" alt="${escapeHtml(item.caption || item.name || "Reisefoto")}"></div><div class="media-gallery-caption"><strong>${escapeHtml(item.caption || item.name || "Foto")}</strong><span>${escapeHtml(stop?.name || day?.title || "Nicht zugeordnet")}</span><small>${escapeHtml(item.taken_at ? this._formatTimestamp(item.taken_at) : "Aufnahmezeit unbekannt")}</small></div></div><div class="modal-actions media-gallery-actions"><button class="icon-button" type="button" data-action="media-gallery-prev"><ha-icon icon="mdi:chevron-left"></ha-icon></button><button class="secondary-button" type="button" data-action="media-edit" data-media-id="${escapeHtml(item.id)}"><ha-icon icon="mdi:pencil-outline"></ha-icon>Zuordnen</button><a class="secondary-button" href="${escapeHtml(this._safeUrl(item.original_url))}" target="_blank" rel="noopener noreferrer"><ha-icon icon="mdi:open-in-new"></ha-icon>Original</a><button class="icon-button" type="button" data-action="media-gallery-next"><ha-icon icon="mdi:chevron-right"></ha-icon></button></div>`;
  },

  _renderImageSearch(dialog) {
    const results = dialog.results || [];
    const isStopGallery = dialog?.context?.targetType === "stop";
    const selected = new Set(dialog.selectedIds || []);
    const providerErrors = dialog.providerErrors && typeof dialog.providerErrors === "object"
      ? Object.entries(dialog.providerErrors)
      : [];
    return `${this._renderModalHeader(isStopGallery ? "Bilder verwalten" : "Zielbild auswählen", dialog.query)}<div class="image-search-body">${results.length ? `<div class="image-search-grid">${results.map((image, index) => {
      const identity = this._imageSearchIdentity(image);
      const active = selected.has(identity);
      const primary = dialog.primaryImageId === identity;
      return `<article class="image-result ${active ? "selected" : ""}">${this._renderDestinationImage({ ...image, context: image.title }, { compact: true })}<div><strong>${escapeHtml(image.title || image.alt)}</strong><span>${escapeHtml(image.attribution || this._destinationProviderLabel(image.provider))}</span><small>${escapeHtml(this._destinationProviderLabel(image.provider))}${image.license ? ` · ${escapeHtml(image.license)}` : ""}</small>${isStopGallery ? `<div class="image-result-actions"><button class="${active ? "secondary-button" : "primary-button"}" type="button" data-action="image-result-toggle" data-image-index="${index}">${active ? "Aus Auswahl entfernen" : "Zur Galerie hinzufügen"}</button><button class="text-button" type="button" data-action="image-result-primary" data-image-index="${index}" ${primary ? "disabled" : ""}><ha-icon icon="mdi:star${primary ? "" : "-outline"}"></ha-icon>${primary ? "Hauptbild" : "Als Hauptbild"}</button></div>` : `<button class="primary-button" type="button" data-action="choose-image" data-image-index="${index}">Dieses Bild verwenden</button>`}</div></article>`;
    }).join("")}</div>` : `<div class="empty-state compact-empty"><ha-icon icon="mdi:image-search-outline"></ha-icon><h2>Keine passenden Bilder gefunden</h2><p>Die Stoppdaten bleiben vollständig erhalten. Du kannst die Suche später erneut versuchen oder eine eigene Bild-URL hinterlegen.</p></div>`}${providerErrors.length ? `<div class="destination-provider-errors"><strong>Nicht alle Bildquellen waren erreichbar</strong>${providerErrors.map(([provider, message]) => `<span><b>${escapeHtml(this._destinationProviderLabel(provider))}:</b> ${escapeHtml(message)}</span>`).join("")}</div>` : ""}<div class="notice info"><ha-icon icon="mdi:information-outline"></ha-icon><div><strong>Bildquellen</strong><span>Roadplanner kombiniert Wikimedia Commons und Openverse, speichert Quellen- und Lizenzangaben und verwendet höchstens drei aktive Planungsbilder pro Stopp.</span></div></div></div><div class="modal-actions"><button class="secondary-button" type="button" data-action="close-dialog">Schließen</button>${isStopGallery ? `<button class="primary-button" type="button" data-action="save-image-gallery" ${selected.size ? "" : "disabled"}><ha-icon icon="mdi:content-save-outline"></ha-icon>${selected.size} ${selected.size === 1 ? "Bild" : "Bilder"} speichern</button>` : ""}</div>`;
  },
};
