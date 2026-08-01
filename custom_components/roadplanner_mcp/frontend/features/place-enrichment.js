import { escapeHtml, cleanText } from "../lib/core-helpers.js";

export const placeEnrichmentMixin = {
  async _preparePlaceEnrichment({ dayId = "", stopId = "", useAiCleanup = false } = {}) {
    const scope = {
      dayId: cleanText(dayId),
      stopId: cleanText(stopId),
      useAiCleanup: Boolean(useAiCleanup),
    };
    const retry = () => this._preparePlaceEnrichment(scope);
    const result = await this._runAction("prepare_place_enrichment", {
      trip_id: this._selectedTripId,
      day_id: scope.dayId,
      stop_id: scope.stopId,
      limit: scope.stopId ? 1 : (scope.dayId ? 12 : 20),
      use_ai_cleanup: scope.useAiCleanup,
    }, "", {
      refresh: false,
      errorMode: "dialog",
      errorTitle: "Ortsprofile konnten nicht vorbereitet werden",
      retry,
    });
    const preview = result?.preview;
    if (!preview || !Array.isArray(preview.items)) return null;
    const selections = {};
    for (const item of preview.items) {
      const stopIdValue = cleanText(item?.current?.stop_id);
      const candidateId = cleanText(item?.selected_candidate_id);
      if (stopIdValue && candidateId) selections[stopIdValue] = candidateId;
    }
    if (result.experience && this._data) {
      this._data = { ...this._data, experience: result.experience };
    }
    this._dialog = {
      type: "place-enrichment",
      preview,
      selections,
      manualEntries: {},
      cleanupConfirmations: {},
      scope,
    };
    this._render({ preserveScroll: true });
    return result;
  },

  async _submitPlaceEnrichment() {
    const dialog = this._dialog;
    if (dialog?.type !== "place-enrichment") return null;
    const selections = dialog.selections || {};
    const retry = () => this._submitPlaceEnrichment();
    const result = await this._runAction("submit_place_enrichment", {
      trip_id: this._selectedTripId,
      preview_id: dialog.preview?.id,
      selections,
      manual_entries: dialog.manualEntries || {},
      cleanup_confirmations: dialog.cleanupConfirmations || {},
    }, "", {
      refresh: false,
      errorMode: "dialog",
      errorTitle: "Ortsprofile konnten nicht übergeben werden",
      retry,
    });
    if (!result) return null;
    this._dialog = null;
    const count = Number(result.operation_count || 0);
    if (result.applied) {
      // The confirmed profiles were applied directly - no second
      // confirmation round under Übergaben, the coordinates are live now.
      await this._loadData({ silent: true, force: true });
      this._showToast(`${count} ${count === 1 ? "Ortsprofil" : "Ortsprofile"} übernommen und angewendet - Kartenpunkte sind aktualisiert`, "success", 6000);
    } else {
      this._activeTab = "handoffs";
      await this._loadData({ silent: true, force: true });
      this._showToast(`${count} ${count === 1 ? "Ortsprofil" : "Ortsprofile"} an die Änderungsübersicht übergeben - dort bitte anwenden${result.apply_error ? ` (Direktübernahme nicht möglich: ${result.apply_error})` : ""}`, result.apply_error ? "error" : "success", 8000);
    }
    this._render({ preserveScroll: false });
    return result;
  },

  _renderPlaceEnrichment(dialog) {
    const preview = dialog?.preview || {};
    const items = Array.isArray(preview.items) ? preview.items : [];
    const selections = dialog?.selections || {};
    const manualEntries = dialog?.manualEntries || {};
    const linkInputs = dialog?.linkInputs || {};
    const cleanupConfirmations = dialog?.cleanupConfirmations || {};
    const selectedCount = Object.values(selections).filter(Boolean).length;
    const assistantConfigured = Boolean(this._data?.settings?.assistant_configured);
    const cleanupDiagnostics = preview.ai_cleanup || {};
    const cleanupRequested = Boolean(preview.use_ai_cleanup || cleanupDiagnostics.requested);
    const cleanupControl = assistantConfigured && !cleanupRequested
      ? `<button class="secondary-button" type="button" data-action="place-enrichment-ai-retry"><ha-icon icon="mdi:creation-outline"></ha-icon>Orte mit KI aufräumen</button>`
      : cleanupRequested
        ? `<span class="status-pill ${cleanupDiagnostics.error ? "status-warning" : "status-resolved"}"><ha-icon icon="mdi:creation-outline"></ha-icon>${cleanupDiagnostics.error ? escapeHtml(cleanupDiagnostics.error) : `${Number(cleanupDiagnostics.suggested_count || 0)} KI-Vorschläge`}</span>`
        : "";
    const placeProviders = this._experienceData().place_providers || {};
    const googleStatus = placeProviders.google_places || {};
    const providerSummary = googleStatus.configured
      ? `<span class="status-pill ${googleStatus.state === "ready" ? "status-resolved" : "status-warning"}"><ha-icon icon="mdi:google-maps"></ha-icon>Google Places ${escapeHtml(placeProviders.mode === "preferred" ? "bevorzugt" : "als Fallback")} · ${Number(googleStatus.requests_today || 0)}/${Number(googleStatus.daily_limit || 0)}</span>`
      : `<span class="status-pill"><ha-icon icon="mdi:map-search-outline"></ha-icon>OpenStreetMap · Google noch nicht eingerichtet</span>`;
    const cards = items.map((item) => {
      const current = item?.current || {};
      const stopId = cleanText(current.stop_id);
      const currentLocation = current.location || {};
      const structured = item?.structured_address || {};
      const intent = item?.destination_intent && typeof item.destination_intent === "object" ? item.destination_intent : {};
      const sourceHints = Array.isArray(intent.source_hints) ? intent.source_hints : [];
      const sourceHintLinks = sourceHints.map((hint) => {
        const url = this._safeUrl(hint?.url);
        if (!url) return "";
        const provider = cleanText(hint?.provider);
        const label = provider === "park4night"
          ? `Park4Night${hint?.id ? ` #${escapeHtml(hint.id)}` : ""} öffnen`
          : provider === "openstreetmap" ? "OpenStreetMap-Hinweis öffnen"
            : provider === "google_maps" ? "Google-Maps-Hinweis öffnen"
              : provider === "wikidata" ? "Wikidata-Hinweis öffnen"
                : provider === "wikipedia" ? "Wikipedia-Hinweis öffnen"
                  : "Quellenhinweis öffnen";
        return `<a class="secondary-button" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer"><ha-icon icon="mdi:open-in-new"></ha-icon>${label}</a>`;
      }).filter(Boolean).join("");
      const candidates = Array.isArray(item?.candidates) ? item.candidates : [];
      const selectedId = cleanText(selections[stopId]);
      const currentSummary = [
        cleanText(currentLocation.label || currentLocation.address),
        Number.isFinite(Number(currentLocation.latitude)) && Number.isFinite(Number(currentLocation.longitude))
          ? `${Number(currentLocation.latitude).toFixed(5)}, ${Number(currentLocation.longitude).toFixed(5)}`
          : "",
      ].filter(Boolean).join(" · ");
      const cleanup = item?.ai_cleanup && typeof item.ai_cleanup === "object" ? item.ai_cleanup : null;
      const cleanupAccepted = Boolean(cleanupConfirmations[stopId]);
      const cleanupName = cleanText(cleanup?.name);
      const currentName = cleanText(current.stop_name);
      const cleanupNameChanged = Boolean(cleanupName && cleanupName.toLocaleLowerCase() !== currentName.toLocaleLowerCase());
      const cleanupKindLabels = {
        address: "Adresse",
        ferry_terminal: "Fährterminal",
        transport_terminal: "Verkehrsterminal",
        hike: "Wanderung",
        nature_center: "Natur- oder Besucherzentrum",
        attraction: "Sehenswürdigkeit",
        retail: "Einkauf",
        restaurant: "Gastronomie",
        camping: "Camping- oder Übernachtungsplatz",
        accommodation: "Unterkunft",
        parking: "Parkplatz",
        fuel: "Tankstelle",
        charging: "Ladepunkt",
        place: "Ort oder POI",
      };
      const cleanupAddress = cleanup?.address || {};
      const cleanupParts = [
        [cleanupAddress.street, cleanupAddress.house_number].filter(Boolean).join(" "),
        [cleanupAddress.postal_code, cleanupAddress.city].filter(Boolean).join(" "),
        cleanupAddress.district,
        cleanupAddress.state,
        cleanupAddress.country || cleanupAddress.country_code,
      ].filter(Boolean);
      const cleanupCard = cleanup
        ? `<div class="place-cleanup-suggestion ${cleanupAccepted ? "selected" : ""}">
            <div><span class="eyebrow">KI-Textanalyse · ohne Koordinaten</span><strong>${escapeHtml(cleanup.name || current.stop_name || "Ortsname beibehalten")}</strong><small>${escapeHtml([
              cleanup.place_kind ? `Zieltyp: ${cleanupKindLabels[cleanup.place_kind] || cleanup.place_kind}` : "",
              cleanupParts.join(", "),
              cleanup.reason,
            ].filter(Boolean).join(" · ") || "Schreibweise und Suchstrategie normalisiert")}</small></div>
            ${cleanupNameChanged ? `<button class="${cleanupAccepted ? "secondary-button" : "text-button"}" type="button" data-action="place-cleanup-toggle" data-stop-id="${escapeHtml(stopId)}"><ha-icon icon="${cleanupAccepted ? "mdi:check-circle" : "mdi:checkbox-blank-circle-outline"}"></ha-icon>${cleanupAccepted ? "Umbenennung wird übernommen" : "Namensvorschlag separat übernehmen"}</button>` : `<span class="status-pill status-resolved"><ha-icon icon="mdi:shape-outline"></ha-icon>Nur Suchstrategie</span>`}
          </div>`
        : "";
      const p4nLookup = item?.p4n_lookup && typeof item.p4n_lookup === "object" ? item.p4n_lookup : null;
      const p4nCard = p4nLookup && Number.isFinite(Number(p4nLookup.latitude)) && Number.isFinite(Number(p4nLookup.longitude))
        ? `<div class="place-cleanup-suggestion place-p4n-suggestion">
            <div><span class="eyebrow">Von der Park4Night-Seite gelesen${p4nLookup.provider === "park4night_page" ? "" : " (KI)"}</span><strong>${escapeHtml(p4nLookup.name || current.stop_name || "Park4Night-Platz")}</strong><small>${escapeHtml([
              `GPS ${Number(p4nLookup.latitude).toFixed(5)}, ${Number(p4nLookup.longitude).toFixed(5)}`,
              p4nLookup.city,
              p4nLookup.price_text,
              p4nLookup.rating_text ? `Bewertung ${p4nLookup.rating_text}` : "",
              p4nLookup.summary,
            ].filter(Boolean).join(" · "))}</small></div>
            <button class="secondary-button" type="button" data-action="place-p4n-apply" data-stop-id="${escapeHtml(stopId)}" data-latitude="${escapeHtml(String(p4nLookup.latitude))}" data-longitude="${escapeHtml(String(p4nLookup.longitude))}" data-name="${escapeHtml(p4nLookup.name || "")}" data-city="${escapeHtml(p4nLookup.city || "")}" data-country-code="${escapeHtml(p4nLookup.country_code || "")}"><ha-icon icon="mdi:map-marker-down"></ha-icon>In den manuellen Kartenpunkt übernehmen</button>
          </div>`
        : cleanText(item?.p4n_lookup_error)
          ? `<div class="notice warning place-p4n-error"><ha-icon icon="mdi:alert-outline"></ha-icon><div><strong>Park4Night-Link erkannt, Seite nicht lesbar</strong><span>${escapeHtml(cleanText(item.p4n_lookup_error))}</span></div></div>`
          : "";
      const candidateCards = candidates.length
        ? candidates.map((candidate) => {
          const candidateId = cleanText(candidate.id);
          const selected = candidateId === selectedId;
          const contact = candidate.contact || {};
          const images = Array.isArray(candidate.images) ? candidate.images.slice(0, 3) : [];
          const location = candidate.location || {};
          const coordinate = Number.isFinite(Number(location.latitude)) && Number.isFinite(Number(location.longitude))
            ? `${Number(location.latitude).toFixed(6)}, ${Number(location.longitude).toFixed(6)}`
            : "—";
          const variantLabel = {
            structured: "Strukturierte Adresse",
            district_text: "Ortsteil-Suche",
            free_text: "Freitext-Suche",
            google_text_search: "Google Places Text Search",
          }[candidate.search_variant] || candidate.search_variant;
          const googleCandidate = candidate.provider === "google_places";
          const placeProviderLabel = googleCandidate ? "Google Places" : candidate.provider === "nominatim" ? "OpenStreetMap" : cleanText(candidate.provider);
          const chips = [
            placeProviderLabel ? `Quelle: ${placeProviderLabel}` : "",
            intent.label ? `Zieltyp: ${intent.label}` : "",
            candidate.match_label,
            candidate.category,
            candidate.confidence_label ? `${candidate.confidence_label} · ${Number(candidate.confidence || 0)} %` : "",
            variantLabel,
            contact.opening_hours ? `Öffnungszeiten: ${contact.opening_hours}` : "",
          ].filter(Boolean);
          return `<article class="place-candidate ${selected ? "selected" : ""}">
            <button class="place-candidate-select" type="button" data-action="place-enrichment-select" data-stop-id="${escapeHtml(stopId)}" data-candidate-id="${escapeHtml(candidateId)}" aria-pressed="${selected ? "true" : "false"}">
              <span class="place-radio"><ha-icon icon="${selected ? "mdi:radiobox-marked" : "mdi:radiobox-blank"}"></ha-icon></span>
              <span><strong>${escapeHtml(candidate.name || candidate.display_name || "Ort")}</strong><small>${escapeHtml(candidate.display_name || candidate.address || "")}</small></span>
            </button>
            ${googleCandidate ? `<div class="google-provider-attribution"><span class="google-maps-label" translate="no">Google Maps</span><span>Google dient als Suchquelle. Nach deiner Bestätigung speichert Roadplanner die Place ID als Referenz und normalisiert das dauerhafte Ortsprofil über OpenStreetMap.</span></div>` : ""}
            ${images.length ? `<div class="place-image-strip">${images.map((image) => `<img loading="lazy" decoding="async" referrerpolicy="no-referrer" src="${escapeHtml(this._safeUrl(image.thumbnail_url || image.image_url))}" alt="${escapeHtml(image.alt || image.title || candidate.name || "Ort")}">`).join("")}</div>` : `<div class="place-image-empty"><ha-icon icon="mdi:image-off-outline"></ha-icon><span>Noch kein passendes Planungsbild</span></div>`}
            <div class="place-candidate-details">
              <div><span>Koordinaten</span><strong>${escapeHtml(coordinate)}</strong></div>
              <div><span>Treffertyp</span><strong>${escapeHtml(candidate.match_label || candidate.category || "Ort")}</strong></div>
              <div><span>Vertrauen</span><strong>${escapeHtml(candidate.confidence_label || "Offen")}${candidate.confidence != null ? ` · ${Number(candidate.confidence || 0)} %` : ""}</strong></div>
              ${candidate.website ? `<div><span>Website</span><a href="${escapeHtml(this._safeUrl(candidate.website))}" target="_blank" rel="noopener noreferrer">Öffnen</a></div>` : ""}
              ${candidate.phone ? `<div><span>Telefon</span><strong>${escapeHtml(candidate.phone)}</strong></div>` : ""}
              ${contact.email ? `<div><span>E-Mail</span><strong>${escapeHtml(contact.email)}</strong></div>` : ""}
            </div>
            ${chips.length ? `<div class="place-chips">${chips.map((chip) => `<span>${escapeHtml(chip)}</span>`).join("")}</div>` : ""}
            <div class="button-row compact-row">
              ${candidate.map_url ? `<a class="secondary-button" href="${escapeHtml(this._safeUrl(candidate.map_url))}" target="_blank" rel="noopener noreferrer"><ha-icon icon="mdi:google-maps"></ha-icon>Karte prüfen</a>` : ""}
              ${candidate.source_url ? `<a class="text-button" href="${escapeHtml(this._safeUrl(candidate.source_url))}" target="_blank" rel="noopener noreferrer">${escapeHtml(candidate.provider === "google_places" ? "Google-Maps-Quelle" : "OpenStreetMap-Quelle")}</a>` : ""}
            </div>
          </article>`;
        }).join("")
        : `<div class="place-no-match"><ha-icon icon="mdi:map-marker-off-outline"></ha-icon><div><strong>Kein Provider-Treffer gefunden</strong><span>${escapeHtml(item?.message || "Adresse und Kartenpunkt können manuell bestätigt werden.")}</span></div></div>`;
      const storedManual = manualEntries[stopId] || {};
      const defaultAddress = cleanText(currentLocation.address) || [
        [structured.street, structured.house_number].filter(Boolean).join(" "),
        [structured.postal_code, structured.city].filter(Boolean).join(" "),
        structured.district,
        structured.state,
        structured.country,
      ].filter(Boolean).join(", ");
      const manualSelected = selectedId === "__manual__";
      const manualForm = `<form class="place-manual-form ${manualSelected ? "selected" : ""}" data-place-manual-form data-stop-id="${escapeHtml(stopId)}">
        <div class="place-manual-heading"><div><span class="eyebrow">Manueller Fallback</span><strong>Adresse und WGS84-Kartenpunkt bewusst bestätigen</strong><small>Diese Werte werden als manuell bestätigt und nicht als Provider-verifiziert gespeichert.</small></div>${manualSelected ? `<span class="status-pill status-resolved">Ausgewählt</span>` : ""}</div>
        <div class="form-grid compact-form-grid">
          ${this._field("name", "Name", storedManual.name ?? current.stop_name ?? "", "text", false, "full")}
          ${this._field("address", "Adresse", storedManual.address ?? defaultAddress, "text", false, "full")}
          ${this._field("city", "Ort", storedManual.city ?? currentLocation.city ?? structured.city ?? "")}
          ${this._field("country_code", "Ländercode", storedManual.country_code ?? currentLocation.country_code ?? structured.country_code ?? "")}
          ${this._field("latitude", "Breitengrad", storedManual.latitude ?? currentLocation.latitude ?? "", "text", true, "", "", "")}
          ${this._field("longitude", "Längengrad", storedManual.longitude ?? currentLocation.longitude ?? "", "text", true, "", "", "")}
        </div>
        <div class="button-row compact-row">
          <button class="secondary-button" type="button" data-action="place-manual-select" data-stop-id="${escapeHtml(stopId)}"><ha-icon icon="mdi:map-marker-check-outline"></ha-icon>Manuellen Kartenpunkt bestätigen</button>
          <button class="text-button" type="button" data-action="place-manual-check-map"><ha-icon icon="mdi:google-maps"></ha-icon>In Google Maps prüfen</button>
        </div>
      </form>`;
      const linkLookup = `<div class="place-link-lookup form-grid compact-form-grid">
        <label class="form-field full"><span>Link zum Ort (Google Maps, Park4Night, Booking, Website …)</span><input type="url" data-place-link-input data-stop-id="${escapeHtml(stopId)}" value="${escapeHtml(linkInputs[stopId] || "")}" placeholder="https://…" autocomplete="off" inputmode="url"></label>
        <div class="form-field full"><button class="secondary-button" type="button" data-action="place-link-lookup" data-stop-id="${escapeHtml(stopId)}"><ha-icon icon="mdi:link-variant"></ha-icon>Link lesen und übernehmen</button><small class="hint">Google-Maps-Links werden direkt aufgelöst, alle anderen Seiten liest der Reisebegleiter (KI). Die Werte landen zum Prüfen im manuellen Kartenpunkt - gespeichert wird erst nach deiner Bestätigung.</small></div>
      </div>`;
      return `<section class="place-enrichment-item">
        <header><div><span class="eyebrow">${escapeHtml([current.day_date, current.day_title].filter(Boolean).join(" · "))}</span><h3>${escapeHtml(current.stop_name || stopId || "Stopp")}</h3><p>${escapeHtml([intent.label ? `Erkannt: ${intent.label}` : "", currentSummary || "Noch kein bestätigtes Ortsprofil"].filter(Boolean).join(" · "))}</p></div><span class="status-pill status-${escapeHtml(item?.status || "missing")}">${escapeHtml(item?.status === "resolved" ? "Eindeutig" : item?.status === "ambiguous" ? "Auswahl nötig" : "Offen")}</span></header>
        ${cleanupCard}
        ${p4nCard}
        ${sourceHintLinks ? `<div class="button-row compact-row">${sourceHintLinks}</div>` : ""}
        <div class="place-candidates">${candidateCards}</div>
        ${linkLookup}
        ${manualForm}
      </section>`;
    }).join("");
    return `${this._renderModalHeader("Stopps anreichern", `${items.length} ${items.length === 1 ? "Stopp" : "Stopps"} · Zieltyp, Kartenpunkt, Ortsprofil und Planungsbilder prüfen`)}
      <div class="place-enrichment-body">
        <div class="place-enrichment-toolbar"><div class="notice info"><ha-icon icon="mdi:shield-check-outline"></ha-icon><div><strong>Geodaten zuerst, Bilder danach</strong><span>Roadplanner erkennt den Zieltyp, sucht einen konkreten Kartenpunkt und verwendet das bestätigte Ortsprofil für kurze, passende Bildanfragen. Zeiten und Stopp-Reihenfolge bleiben unverändert.</span></div></div><div class="button-row compact-row">${providerSummary}${cleanupControl}</div><details class="provider-ranking-info"><summary>Wie werden die Treffer sortiert?</summary><p>Jeder Provider liefert zunächst seine eigene Ergebnisreihenfolge. Roadplanner bewertet die Kandidaten anschließend providerübergreifend anhand von Namen, Zieltyp, Adresse, Land, vorhandener Standortnähe und Sicherheitsgrenzen. Ein schwacher Stadt- oder Ortsteiltreffer verdrängt dabei keinen konkreten POI. Google wird je nach Einrichtung bevorzugt oder nur als Fallback aufgerufen.</p></details></div>
        ${cards || `<div class="empty-state compact-empty"><ha-icon icon="mdi:map-marker-check-outline"></ha-icon><h2>Keine offenen Ortsprofile</h2></div>`}
      </div>
      <div class="modal-actions place-enrichment-actions"><button class="secondary-button" type="button" data-action="close-dialog">Abbrechen</button><button class="primary-button" type="button" data-action="place-enrichment-submit" ${selectedCount ? "" : "disabled"}><ha-icon icon="mdi:clipboard-check-outline"></ha-icon>${selectedCount} ${selectedCount === 1 ? "Stopp" : "Stopps"} an Änderungsübersicht übergeben</button></div>`;
  },
};

Object.assign(placeEnrichmentMixin, {
  async _runPlaceLinkLookup(stopId) {
    if (this._dialog?.type !== "place-enrichment" || !stopId) return;
    const input = this.shadowRoot.querySelector(`[data-place-link-input][data-stop-id='${stopId}']`);
    const url = cleanText(input?.value);
    if (!url) {
      this._showToast("Bitte zuerst einen Link einfügen (https://…).", "error", 5000);
      return;
    }
    // Keep the typed link across re-renders regardless of the outcome.
    this._dialog.linkInputs = { ...(this._dialog.linkInputs || {}), [stopId]: url };
    const result = await this._runAction("place_link_lookup", { url }, "", {
      refresh: false,
      errorTitle: "Link konnte nicht gelesen werden",
    });
    const facts = result?.result;
    if (!facts || this._dialog?.type !== "place-enrichment") return;
    // Prefill only - the manual confirmation stays the user's explicit step,
    // stored as manually confirmed, never as provider-verified.
    const entry = { ...(this._dialog.manualEntries?.[stopId] || {}) };
    if (facts.name) entry.name = facts.name;
    if (facts.city) entry.city = facts.city;
    if (facts.country_code) entry.country_code = facts.country_code;
    const hasCoordinates = facts.latitude != null && facts.longitude != null;
    if (hasCoordinates) {
      entry.latitude = String(facts.latitude);
      entry.longitude = String(facts.longitude);
    }
    this._dialog.manualEntries = { ...(this._dialog.manualEntries || {}), [stopId]: entry };
    if (hasCoordinates) {
      this._dialog.selections = { ...(this._dialog.selections || {}), [stopId]: "__manual__" };
    }
    const summary = [facts.price_text, facts.rating_text ? `Bewertung ${facts.rating_text}` : "", facts.summary]
      .filter(Boolean).join(" · ");
    this._showToast(
      hasCoordinates
        ? `Vom Link übernommen${summary ? ` (${summary})` : ""} - bitte prüfen und bestätigen.`
        : "Der Link nannte keine GPS-Position - Name/Ort wurden vorbefüllt, bitte Kartenpunkt ergänzen.",
      hasCoordinates ? "success" : "error",
      6500,
    );
    this._render({ preserveScroll: true });
  },
});
