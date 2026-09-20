export type Health = {
  status: string;
  checks: Record<string, { ok: boolean; ms?: number; error?: string; backend?: string }>;
  worker_tags: string[];
  display_currency: string;
  tls_port: number;
  // When on, the app leads with scanning: crop, render, export. No identification, no pricing.
  scanner_mode?: boolean;
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

  removeCornerDetails: (skus?: string[]) =>
    del<{ removed: number }>(
      `/api/ebay/details${skus?.length ? `?skus=${skus.join(",")}` : ""}`,
    ),

  buildCornerDetails: (sku: string, fraction?: number) =>
    post<EbayCard & { detail_result: { rendered: string[]; reason: string | null } }>(
      `/api/ebay/${sku}/details${fraction ? `?fraction=${fraction}` : ""}`,
      {},
    ),

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
        current: boolean;
        cards: number;
      }[];
      current_id: string | null;
      unassigned: number;
    }>("/api/sessions"),

  startSession: (name?: string, photosOnly = false) =>
    post<{ id: string; name: string; photos_only: boolean }>("/api/sessions", {
      name,
      photos_only: photosOnly,
    }),

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
