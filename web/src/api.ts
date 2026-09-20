export type Health = {
  status: string;
  checks: Record<string, { ok: boolean; ms?: number; error?: string; backend?: string }>;
  worker_tags: string[];
  display_currency: string;
  tls_port: number;
  // When on, the app leads with scanning: crop, render, export. No identification, no pricing.
  scanner_mode?: boolean;
  // False means this instance is serving every endpoint without a password. The shell says so
  // in a banner; an unauthenticated deployment must never be the quiet state.
  auth_required?: boolean;
};

export type Stats = { sets: number; cards: number; variants: number };

export type Job = {
  id: string;
  type: string;
  status: string;
  progress: number;
  progress_total: number | null;
  result: Record<string, unknown> | null;
  error: string | null;
  created_at: string;
};

export type Rubric = {
  ceilings: Record<string, number>;
  severity_points: Record<string, number>;
  imperfections: { key: string; label: string; measure: string }[];
};

/** The API's own explanation of a failure, or the status if it did not give one.
 *
 * The endpoints here answer failures with a `detail` that says what to do about it — "set
 * S3_ENDPOINT…", "identify the card first" — and throwing that away in favour of
 * "/api/x -> 409" turns every one of them into a puzzle. */
async function failure(path: string, response: Response): Promise<Error> {
  // A cookie that expired mid-session otherwise surfaces as "log in first" written across
  // whichever screen happened to be polling, which reads as a broken app rather than a
  // finished session. The shell listens for this and puts the login screen back.
  if (response.status === 401) {
    window.dispatchEvent(new CustomEvent("tcg-unauthenticated"));
  }
  try {
    const body = await response.json();
    if (typeof body?.detail === "string") return new Error(body.detail);
    // FastAPI's validation errors arrive as a list of objects.
    if (Array.isArray(body?.detail)) {
      const first = body.detail[0];
      if (first?.msg) return new Error(`${first.msg} (${(first.loc ?? []).join(".")})`);
    }
  } catch {
    /* non-JSON error body; fall through to the status */
  }
  return new Error(`${path} -> ${response.status}`);
}

async function patch<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw await failure(path, response);
  return response.json() as Promise<T>;
}

async function del<T>(path: string): Promise<T> {
  const response = await fetch(path, { method: "DELETE" });
  if (!response.ok) throw await failure(path, response);
  return response.json() as Promise<T>;
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) throw await failure(path, response);
  return response.json() as Promise<T>;
}

async function postForm<T>(path: string, form: FormData): Promise<T> {
  const response = await fetch(path, { method: "POST", body: form });
  if (!response.ok) throw await failure(path, response);
  return response.json() as Promise<T>;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw await failure(path, response);
  return response.json() as Promise<T>;
}

export type Pending = {
  next_side: "front" | "back";
  awaiting_back: string | null;
  items: number;
  open_image_reviews: number;
};

export type SideState = {
  captured: boolean;
  processed: boolean;
  manual: boolean;
  confidence: number | null;
  verdict: "ok" | "review" | "reject" | null;
  error: string | null;
};

export type RecentItem = {
  sku: string;
  created_at: string;
  /** This card's batch crops and renders but never identifies. */
  photos_only: boolean;
  /** Extra shots already on this card, in slot order. */
  extras: { slot: number; url: string | null }[];
  has_front: boolean;
  has_back: boolean;
  processed_front: boolean;
  processed_back: boolean;
  front: SideState;
  back: SideState;
  thumbnail: string | null;
  confidence: number | null;
  verdict: "ok" | "review" | "reject" | null;
  errors: string[];
  status: string;
  listing_front: string | null;
  listing_back: string | null;
  processed_front_url: string | null;
  processed_back_url: string | null;
  card: string | null;
  card_number: string | null;
  variant: string | null;
  identified_confidence: number | null;
};

export type CaptureResponse = {
  sku: string;
  side: string;
  created_item: boolean;
  next_side: "front" | "back";
};

export type SideDetail = {
  original: string;
  width: number | null;
  height: number | null;
  error: string | null;
  processed: string | null;
  corners: number[][] | null;
  confidence: number | null;
  method: string | null;
  manual: boolean;
  verdict: string | null;
  quality: Record<string, unknown> | null;
};

export type ImageSlot = {
  label: string;
  url: string | null;
  state: "ready" | "pending" | "failed" | "uncaptured";
  note: string | null;
};

export type ItemDetail = {
  sku: string;
  sides: Record<string, SideDetail>;
  card?: { tcgdex_id: string; name: string; local_id?: string } | null;
  variant?: string | null;
  confidence?: number | null;
  images?: ImageSlot[];
};

export const api = {
  health: () => get<Health>("/health/detail"),
  stats: () => get<Stats>("/api/catalog/stats"),
  jobs: () => get<Job[]>("/api/jobs?limit=8"),
  rubric: () => get<Rubric>("/api/conditioning/rubric"),
  startSync: (limitSets?: number) =>
    post<{ job_id: string }>("/api/jobs/sync", { limit_sets: limitSets ?? null }),
  startPrices: (limit?: number) =>
    post<{ job_id: string }>("/api/jobs/prices", { limit: limit ?? null }),

  pending: () => get<Pending>("/api/capture/pending"),
  recent: () => get<RecentItem[]>("/api/capture/recent"),

  captureSide: (blob: Blob, side: "front" | "back", source: string) => {
    const form = new FormData();
    form.append("file", blob, `${side}.jpg`);
    form.append("side", side);
    form.append("source", source);
    return postForm<CaptureResponse>("/api/capture", form);
  },

  /** Attach an extra shot to the card being worked on — the newest one. The quick path from
   *  the phone screen; the response names the card it landed on. */
  captureExtra: (blob: Blob, source = "webcam") => {
    const form = new FormData();
    form.append("file", blob, "extra.jpg");
    form.append("source", source);
    return postForm<{
      sku: string;
      slot: string;
      url: string | null;
      extras: number;
      remaining: number;
    }>("/api/capture/extra", form);
  },

  /** Attach an extra shot to a named card. Takes a File from a picker or a Blob from a
   *  camera frame — the extra pass shoots straight into this. */
  captureExtraFor: (sku: string, file: Blob, source = "upload") => {
    const form = new FormData();
    form.append("file", file, file instanceof File ? file.name : "extra.jpg");
    form.append("source", source);
    return postForm<{
      sku: string;
      slot: string;
      url: string | null;
      extras: number;
      remaining: number;
    }>(`/api/capture/${sku}/extra`, form);
  },

  deleteExtra: (sku: string, number: number) =>
    del<{ sku: string; removed: number }>(`/api/capture/${sku}/extra/${number}`),

  /** Preferences, what this install is, and how much is in it. */
  settings: () =>
    get<{
      preferences: {
        intro_done: boolean;
        intro_step: number;
        corner_shots_default: boolean;
      };
      configuration: {
        mode: string;
        mode_source: string;
        authentication: string;
        authentication_source: string;
        session_days: number;
        storage: string;
        listing_px_per_mm: number;
        https_port: number;
      };
      collection: { cards: number; batches: number; photographs: number };
      version: string;
    }>("/api/settings"),

  saveSettings: (changes: {
    intro_done?: boolean;
    intro_step?: number;
    corner_shots_default?: boolean;
  }) => patch<{ preferences: Record<string, unknown> }>("/api/settings", changes),

  // ── the front door ───────────────────────────────────────────────────────────────────

  /** Which of the three states this instance is in. Never 401s, so it is safe to call from
   *  the login screen itself. */
  authStatus: () =>
    get<{
      required: boolean;
      claimed: boolean;
      authenticated: boolean;
      has_password: boolean;
      open_by_choice: boolean;
      has_recovery_code: boolean;
      min_password_length: number;
    }>("/api/auth/status"),

  /** Finish first-run setup. `null` means a deliberate decision to have no password —
   *  different from an instance nobody has set up, which serves nothing at all. */
  claimInstance: (password: string | null, label?: string) =>
    post<{ claimed: boolean; password: boolean; recovery_code?: string }>(
      "/api/auth/claim",
      password === null ? {} : { password, label },
    ),

  /** Set a new password using the recovery code. Signs every device out. */
  recoverWithCode: (code: string, newPassword: string) =>
    post<{ recovered: boolean; recovery_code: string }>("/api/auth/recover", {
      code,
      new_password: newPassword,
    }),

  /** A fresh recovery code, invalidating the old one. Readable exactly once. */
  regenerateRecovery: () =>
    post<{ recovery_code: string }>("/api/auth/recovery-code", {}),

  /** Put a password on an instance that was set up without one. */
  requirePassword: (password: string, label?: string) =>
    post<{ password: boolean; recovery_code: string }>("/api/auth/require-password", {
      password,
      label,
    }),

  login: (password: string, label?: string) =>
    post<{ authenticated: boolean; days: number }>("/api/auth/login", { password, label }),

  logout: () => post<{ logged_out: boolean }>("/api/auth/logout", {}),

  changePassword: (current: string, next: string) =>
    post<{ changed: boolean; other_devices_signed_out: number }>("/api/auth/password", {
      current,
      new: next,
    }),

  devices: () =>
    get<{
      devices: {
        id: string;
        label: string | null;
        created_at: string;
        last_seen_at: string | null;
        expires_at: string;
        this_one: boolean;
      }[];
    }>("/api/auth/devices"),

  revokeDevice: (id: string) => del<{ revoked: string }>(`/api/auth/devices/${id}`),

  /** What a download of this batch would contain, grouped by photographs per card — the
   *  number a bulk uploader has to be told. */
  photoGroups: (sessionId: string) =>
    get<{
      session_id: string;
      name: string;
      corners: boolean;
      cards: number;
      groups: { photos: number; cards: number; skus: string[]; folder: string }[];
      without_photos: string[];
      uploads_needed: number;
    }>(`/api/sessions/${sessionId}/photo-groups`),

  renameSession: (id: string, name: string) =>
    patch<{ id: string; name: string; note: string | null }>(`/api/sessions/${id}`, { name }),

  searchCards: (q: string) =>
    get<
      {
        tcgdex_id: string;
        name: string;
        local_id?: string;
        number?: string;
        set?: { tcgdex_id: string; name: string; total?: number };
      }[]
    >(`/api/catalog/cards?q=${encodeURIComponent(q)}&limit=12`),

  setCard: (sku: string, tcgdexId: string) =>
    post<{ ok: boolean; name: string }>(`/api/capture/${sku}/card`, { tcgdex_id: tcgdexId }),

  variants: (sku: string) =>
    get<{
      sku: string;
      current: string | null;
      variants: { id: string; label: string; source: string }[];
      addable: string[];
      catalogue: "generated" | "partial" | "known";
    }>(`/api/capture/${sku}/variants`),

  setVariant: (sku: string, variantId: string) =>
    post<{ ok: boolean; variant: string | null }>(`/api/capture/${sku}/variant`, {
      variant_id: variantId,
    }),

  setManualPrice: (variantId: string, amount: number) =>
    post<{ ok: boolean; amount: number }>(`/api/catalog/variants/${variantId}/price`, {
      amount,
      currency: "USD",
    }),

  setVariantFinish: (sku: string, finish: string) =>
    post<{ ok: boolean; variant: string | null }>(`/api/capture/${sku}/variant`, { finish }),

  assessment: (sku: string) =>
    get<{
      condition: string | null;
      points: number | null;
      accepted: { defects: { imperfection: string; severity: string }[] } | null;
      history: unknown[];
    }>(`/api/conditioning/${sku}/assessment`),

  setCondition: (sku: string, condition: string) =>
    post<{ ok: boolean; condition: string }>(`/api/conditioning/${sku}/condition`, {
      condition,
    }),

  clearCondition: (sku: string) =>
    post<{ ok: boolean }>(`/api/conditioning/${sku}/condition/clear`, {}),



  approvalQueue: () => get<ApprovalQueue>("/api/review/approval"),

  setAside: (sku: string) =>
    post<{ ok: boolean }>(`/api/review/approval/${sku}/set-aside`, {}),

  approvalNeedsRescan: (sku: string) =>
    post<{ ok: boolean }>(`/api/review/approval/${sku}/needs-rescan`, {}),

  restoreSetAside: () =>
    post<{ ok: boolean; restored: number }>("/api/review/approval/restore", {}),

  unapprove: (sku: string) =>
    post<{ ok: boolean }>(`/api/review/approval/${sku}/unapprove`, {}),

  approve: (sku: string) =>
    post<{ ok: boolean; settled: number }>(`/api/review/approval/${sku}`, {}),


  listingDraft: (sku: string) =>
    get<{ template_item_id: string | null; draft: ListingDraft }>(
      `/api/inventory/${sku}/listing`,
    ),

  ebayQueue: (show: "ready" | "blocked" | "listed" | "all") =>
    get<{ cards: EbayCard[]; counts: EbayCounts; value_ready: number }>(
      `/api/ebay/queue?show=${show}`,
    ),

  saveEbayDraft: (sku: string, body: { title?: string; price?: number }) =>
    post<EbayCard>(`/api/ebay/${sku}/draft`, body),

  resetEbayDraft: (sku: string) => post<EbayCard>(`/api/ebay/${sku}/draft/reset`, {}),

  markEbayListed: (sku: string, listed: boolean) =>
    post<EbayCard>(`/api/ebay/${sku}/listed`, { listed }),

  fetchEbaySold: (skus: string[]) =>
    post<{
      priced: {
        sku: string;
        query: string;
        found: number;
        usable: number;
        price: { median: number; low: number; high: number; count: number } | null;
      }[];
      failed: { sku: string; error: string }[];
      skipped: { sku: string; why: string }[];
    }>("/api/jobs/ebay-sold", { skus }),

  publishPhotos: (show = "ready") =>
    post<{ uploaded: number; failed: { key: string; error: string }[]; base: string; cards: number }>(
      `/api/ebay/publish-photos?show=${show}`,
      {},
    ),

  exportCheck: (query: string) =>
    get<{
      ok: boolean;
      template: string | null;
      rows: number;
      missing_required: string[];
      dropped: string[];
      skipped: string[];
    }>(`/api/ebay/export/check?${query}`),

  photoCheck: (show = "ready") =>
    get<{ ok: boolean; reason: string; host?: string; failures?: number }>(
      `/api/ebay/photo-check?show=${show}&limit=2`,
    ),

  photoHost: () =>
    get<{
      url: string | null;
      running: boolean;
      live: boolean | null;
      object_storage: boolean;
      error?: string;
    }>("/api/ebay/photo-host"),

  buildAllCornerDetails: (show: string) =>
    post<{ built: string[]; already_had_them: string[]; threshold: number }>(
      `/api/ebay/details/bulk?show=${show}`,
      {},
    ),

  /** Re-cut one card's corner close-ups. On the capture router, not eBay's: cutting a corner
   *  out of a photograph is not a selling operation, and the eBay routes do not exist at all
   *  in scanner mode. */
  buildCornerDetails: (sku: string, fraction?: number) =>
    post<{ sku: string; fraction: number; rendered: string[]; reason: string | null }>(
      `/api/capture/${sku}/detail-shots${fraction ? `?fraction=${fraction}` : ""}`,
      {},
    ),

  /** Turn corner close-ups on or off for a batch. Turning them on also cuts the ones that are
   *  missing; turning them off never deletes anything. */
  setBatchCorners: (sessionId: string, on: boolean, fraction?: number) =>
    post<{
      session_id: string;
      corner_shots: boolean;
      built: string[];
      already_had_them: string[];
    }>(`/api/sessions/${sessionId}/corners`, { on, fraction }),

  /** Delete a batch's corner close-ups from disk. Explicit and separate from the switch. */
  deleteBatchCorners: (sessionId: string) =>
    del<{ session_id: string; removed: number }>(`/api/sessions/${sessionId}/corners`),

  setListingTemplate: (sku: string, reference: string) =>
    post<{ ok: boolean; item_id: string }>(
      `/api/inventory/${sku}/listing/template`,
      { reference },
    ),

  sessions: () =>
    get<{
      sessions: {
        id: string;
        name: string;
        note: string | null;
        started_at: string;
        closed_at: string | null;
        open: boolean;
        photos_only: boolean;
        corner_shots: boolean;
        current: boolean;
        cards: number;
      }[];
      current_id: string | null;
      unassigned: number;
    }>("/api/sessions"),

  startSession: (name?: string, photosOnly = false, open = true) =>
    post<{ id: string; name: string; photos_only: boolean; open: boolean }>("/api/sessions", {
      name,
      photos_only: photosOnly,
      open,
    }),

  /** Move cards into a batch. Grouping only — no photograph is touched. */
  moveCards: (sessionId: string, skus: string[]) =>
    post<{
      session_id: string;
      name: string;
      moved: number;
      missing: string[];
      sources: string[];
    }>(`/api/sessions/${sessionId}/cards`, { skus }),

  archiveSession: (id: string, name?: string) =>
    post<{
      archived: { id: string; name: string; cards: number };
      now_scanning_into: { id: string; name: string };
    }>(`/api/sessions/${id}/archive${name ? `?name=${encodeURIComponent(name)}` : ""}`, {}),

  reopenSession: (id: string) =>
    post<{ id: string; name: string }>(`/api/sessions/${id}/reopen`, {}),

  deleteSession: (id: string, cards: "keep" | "delete") =>
    del<{ deleted: string; cards_removed: number }>(
      `/api/sessions/${id}?cards=${cards}`,
    ),

  swapSides: (sku: string) =>
    post<{ sku: string; derived_removed: number; jobs: string[] }>(
      `/api/capture/${sku}/sides/swap`,
      {},
    ),

  deleteCards: (skus: string[]) =>
    post<{ deleted: number; files_removed: number }>("/api/inventory/delete", { skus }),

  inventorySets: () =>
    get<{ sets: { name: string; count: number }[] }>("/api/inventory/sets"),

  autoLots: (body: {
    by: "set" | "mixed";
    verdict: string | null;
    max_per_lot: number;
    dry_run: boolean;
  }) =>
    post<{
      created: { id: string | null; name: string; cards: number; skus: string[] }[];
      would_lot: number;
      dry_run: boolean;
    }>("/api/inventory/lots/auto", body),

  inventory: (
    verdict?: string,
    unlotted?: boolean,
    extra?: Record<string, string>,
  ) => {
    const q = new URLSearchParams();
    if (verdict) q.set("verdict", verdict);
    if (unlotted) q.set("unlotted", "true");
    for (const [k, v] of Object.entries(extra ?? {})) if (v) q.set(k, v);
    return get<{ cards: InventoryRow[]; totals: Record<string, unknown> }>(
      `/api/inventory${q.toString() ? `?${q}` : ""}`,
    );
  },

  lots: () => get<LotRow[]>("/api/inventory/lots"),

  createLot: (name: string) =>
    post<{ id: string; name: string }>("/api/inventory/lots", { name }),

  addToLot: (lotId: string, skus: string[]) =>
    post<{ ok: boolean }>(`/api/inventory/lots/${lotId}/cards`, { skus }),

  removeFromLot: (lotId: string, skus: string[]) =>
    post<{ ok: boolean }>(`/api/inventory/lots/${lotId}/remove`, { skus }),

  setLotPrice: (lotId: string, askingPrice: number | null) =>
    post<{ ok: boolean }>(`/api/inventory/lots/${lotId}/price`, {
      asking_price: askingPrice,
    }),

  deleteLot: (lotId: string) =>
    del<{ ok: boolean }>(`/api/inventory/lots/${lotId}`),

  progress: () =>
    get<{
      total: number;
      stages: { name: string; done: number; outstanding: number; phase: number }[];
      blockers: Record<string, { open: number; later: number }>;
      next_up: string | null;
    }>("/api/review/progress"),

  reviewQueue: () => get<ReviewQueue>("/api/review/queue"),

  resolveReview: (reviewId: string, choice: string | null, dismiss: boolean) =>
    post<{ ok: boolean; status: string; applied: string | null }>(
      `/api/review/${reviewId}/resolve`,
      { choice, dismiss },
    ),

  requestRescan: (reviewId: string) =>
    post<{ ok: boolean; sku: string }>(`/api/review/${reviewId}/rescan`, {}),

  clearRescan: (sku: string) =>
    post<{ ok: boolean }>(`/api/review/rescan/${sku}/clear`, {}),

  deferReview: (reviewId: string) =>
    post<{ ok: boolean }>(`/api/review/${reviewId}/defer`, {}),

  resumeReview: (reviewId: string) =>
    post<{ ok: boolean }>(`/api/review/${reviewId}/resume`, {}),

  clearImageReviews: () =>
    post<{ ok: boolean; dismissed: number }>("/api/review/resolve-all-images", {}),

  captureBatch: (files: File[]) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f, f.name));
    form.append("source", "upload");
    return postForm<{ accepted: number; results: unknown[] }>("/api/capture/batch", form);
  },

  recognise: (sku: string) =>
    post<{ ok: boolean; name: string | null; confidence: number; needs_review: boolean }>(
      `/api/capture/${sku}/recognize`,
      {},
    ),

  reprocess: (sku: string) =>
    post<{ sku: string; queued: number }>(`/api/capture/${sku}/reprocess`, {}),

  item: (sku: string) => get<ItemDetail>(`/api/capture/${sku}`),

  rectifyCorners: (sku: string, side: string, corners: number[][]) =>
    post<{ ok: boolean; verdict: string; needs_review: boolean }>(
      `/api/capture/${sku}/${side}/corners`,
      { corners },
    ),

  captureFile: (file: File, side: "front" | "back", source: string) => {
    const form = new FormData();
    form.append("file", file, file.name || `${side}.jpg`);
    form.append("side", side);
    form.append("source", source);
    return postForm<CaptureResponse>("/api/capture", form);
  },
};

export type OpenReview = {
  id: string;
  category: string;
  reason: string | null;
  candidates: Record<string, unknown>[];
  created_at: string;
};

export type ReviewItem = {
  sku: string;
  later?: OpenReview[];
  images: { front: string | null; back: string | null };
  card: { tcgdex_id: string; name: string; set: string } | null;
  reviews: OpenReview[];
  blocking: boolean;
};

export type ReviewQueue = {
  needs_you: ReviewItem[];
  later: ReviewItem[];
  rescan: { sku: string; images: { front: string | null; back: string | null } }[];
  clean: { sku: string; name: string | null; confidence: number; thumbnail: string | null }[];
  counts: {
    needs_you: number;
    later: number;
    rescan: number;
    blocking: number;
    clean: number;
  };
};

export type ApprovalQueue = {
  remaining: number;
  approved: number;
  set_aside: number;
  awaiting_rescan: number;
  zoom: { label: string; x: number; y: number; w: number; h: number }[];
  card: {
    sku: string;
    images: ImageSlot[];
    identified: {
      tcgdex_id: string;
      name: string;
      local_id: string | null;
      set: string | null;
      set_total: number | null;
    } | null;
    confidence: number;
    variant: string | null;
    variant_id: string | null;
    condition: string | null;
    condition_points: number | null;
    value: {
      amount: number | null;
      currency: string;
      source: string | null;
      age_days: number | null;
      stale: boolean;
      condition: string | null;
      multiplier: number | null;
      adjusted: number | null;
      ebay_estimate: number | null;
      floored: boolean;
      net: number | null;
      verdict: string | null;
      break_even: number | null;
    };
    flags: { category: string; reason: string | null }[];
    missing: string[];
  } | null;
};

export type InventoryRow = {
  sku: string;
  name: string | null;
  set: string | null;
  number: string | null;
  variant: string | null;
  condition: string | null;
  approved: boolean;
  lot: string | null;
  session_id: string | null;
  thumbnail: string | null;
  /** How many extra shots this card carries (0-3). */
  extras: number;
  value: {
    ebay_estimate: number | null;
    net: number | null;
    verdict: string | null;
  };
};

export type LotRow = {
  id: string;
  name: string;
  note: string | null;
  asking_price: number | null;
  skus: string[];
  economics: {
    cards: number;
    suggested_price: number;
    net: number;
    net_if_sold_separately: number;
    advantage: number;
  };
};

export type EbayCounts = {
  ready: number;
  listed: number;
  blocked: number;
  all: number;
};

export type EbayCard = {
  sku: string;
  name: string | null;
  number: string;
  set_name: string;
  variant: string | null;
  image: string | null;
  image_back: string | null;
  detail_images: string[];
  wants_details: boolean;
  title: string;
  generated_title: string;
  title_edited: boolean;
  check_set_name: boolean;
  price: number | null;
  generated_price: number | null;
  price_edited: boolean;
  condition_label: string | null;
  condition_code: string | null;
  specifics: Record<string, string>;
  description: string;
  template_item_id: string | null;
  template_url: string | null;
  sold_search_url: string | null;
  listed_at: string | null;
  blockers: string[];
};

export type ListingDraft = {
  title: string;
  title_length: number;
  description: string;
  condition_label: string | null;
  condition_code: string | null;
  price: number | null;
  template_url: string | null;
  sold_search_url: string | null;
  specifics: Record<string, string>;
};
