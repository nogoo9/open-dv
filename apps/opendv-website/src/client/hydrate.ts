type El = HTMLElement;

const doc = document;
const q = (s: string, r: ParentNode = doc) => r.querySelector<El>(s);
const qa = (s: string, r: ParentNode = doc) => [...r.querySelectorAll<El>(s)];
const id = (k?: string | null) => (k ? doc.getElementById(k) : null);
const hit = (e: Event, s: string) =>
	e.target instanceof Element ? e.target.closest<El>(s) : null;
const sa = (el: Element, k: string, v: string | null) =>
	v === null ? el.removeAttribute(k) : el.setAttribute(k, v);

const TAB = '[role="tab"]';
const FAULTS: Record<string, string> = {
	"fault-bk-collision": "L1",
	"fault-pii-split": "V6B",
	"fault-hub-edge": "V1_V5",
};

function sync(r: ParentNode, a: string, v: string, det?: string) {
	for (const b of qa(`[${a}]`, r))
		sa(b, "aria-pressed", String(b.getAttribute(a) === v));
	if (!det) return;
	for (const n of qa(`[${det}]`, r)) {
		const on = n.getAttribute(det) === v;
		if (det === "data-pin-detail") n.dataset.active = String(on);
		else n.hidden = !on;
	}
}

function tabCtx(t: El | null): [El, El] | null {
	const l = t?.closest<El>('[role="tablist"]');
	return t && l ? [t, l] : null;
}

function activateTab(list: El, sel: El, focus: boolean) {
	const dia = list.closest<El>('[data-diagram="inference-matrix"]');
	for (const t of qa(TAB, list)) {
		const on = t === sel;
		sa(t, "aria-selected", String(on));
		t.tabIndex = on ? 0 : -1;
		const p = id(t.dataset.tabTarget);
		if (p) {
			sa(p, "hidden", on ? null : "until-found");
			sa(p, "tabindex", on ? "0" : null);
		}
		const sid = t.dataset.scenarioTab;
		const sp = dia && sid ? q(`[data-scenario-panel="${sid}"]`, dia) : null;
		if (dia && sid && on) dia.dataset.activeScenario = sid;
		if (sp) sp.hidden = !on;
	}
	sel.scrollIntoView({ block: "nearest", inline: "nearest" });
	if (focus) sel.focus();
}

function copyText(b: El, txt: string) {
	const ok = () => {
		if (b.dataset.copied) return;
		const orig = b.textContent;
		b.textContent = "Copied!";
		b.dataset.copied = "true";
		setTimeout(() => {
			b.textContent = orig;
			delete b.dataset.copied;
		}, 1600);
	};
	const fb = () => {
		const ta = doc.body.appendChild(doc.createElement("textarea"));
		ta.value = txt;
		ta.select();
		if (doc.execCommand("copy")) ok();
		ta.remove();
	};
	if (navigator.clipboard) navigator.clipboard.writeText(txt).then(ok, fb);
	else fb();
}

function openLightbox(tr: El) {
	const dlg = id("media-lightbox");
	const st = dlg && q("[data-lightbox-stage]", dlg);
	if (!(dlg instanceof HTMLDialogElement) || !st) return;
	const d = tr.dataset;
	let cap = d.lightboxTitle;
	if (d.lightboxImage) {
		const img = doc.createElement("img");
		img.src = d.lightboxImage;
		img.alt = cap ?? "Screenshot";
		st.replaceChildren(img);
	} else {
		const svg = q(`[data-lightbox-source="${d.lightboxOpen}"] svg`);
		const clone = svg?.cloneNode(true);
		if (!svg || !(clone instanceof SVGElement)) return;
		cap ??= q("title", svg)?.textContent?.trim();
		sa(clone, "aria-labelledby", null);
		sa(clone, "aria-label", cap ?? "Diagram");
		for (const c of qa("[id]", clone)) c.removeAttribute("id");
		st.replaceChildren(clone);
	}
	const c = q("[data-lightbox-caption]", dlg);
	if (c) c.textContent = cap ?? "Open-DV Preview";
	dlg.showModal();
}

function handleClick(e: Event) {
	if (hit(e, "#site-menu a")) id("site-menu")?.hidePopover?.();

	const cp = hit(e, "[data-copy-text]");
	if (cp?.dataset.copyText) return void copyText(cp, cp.dataset.copyText);

	const tc = tabCtx(hit(e, TAB));
	if (tc) return void activateTab(tc[1], tc[0], false);

	const pin = hit(e, "[data-pin-btn]");
	const sc = pin?.closest<El>("[data-pin-scope]");
	if (sc && pin?.dataset.pinBtn)
		return void sync(sc, "data-pin-btn", pin.dataset.pinBtn, "data-pin-detail");

	const fw = hit(e, "[data-firewall-select]");
	const fwF = fw?.closest<El>('[data-diagram="vault-firewall"]');
	const m = fw?.dataset.firewallSelect;
	if (fwF && m) {
		fwF.dataset.firewallMode = m;
		sync(fwF, "data-firewall-select", m);
		const cap = q("[data-firewall-caption]", fwF);
		if (cap)
			cap.textContent =
				m === "naive"
					? "Naive LLM SQL bypasses formal CEGAR gates."
					: "Open-DV pairs brokered Agents with L1 & V1–V8 CEGAR proofs before compiling 11 storage targets.";
		return;
	}

	const fl = hit(e, "[data-fault-btn]")?.dataset.faultBtn;
	const gt = hit(e, "[data-gate-select]")?.dataset.gateSelect;
	const sg = (fl || gt) && hit(e, '[data-diagram="six-gate-verifier"]');
	if (sg) {
		if (fl) {
			sg.dataset.activeFault = fl;
			sync(sg, "data-fault-btn", fl, "data-fault-detail");
		}
		const gid = gt ?? (fl ? FAULTS[fl] : undefined);
		if (gid) {
			sg.dataset.activeGate = gid;
			sync(sg, "data-gate-select", gid, "data-gate-detail");
		}
		return;
	}

	const pb = hit(e, "[data-diagram-pause]");
	const pf = pb?.closest<El>("[data-paused]");
	if (pb && pf) {
		const p = pf.dataset.paused !== "true";
		pf.dataset.paused = String(p);
		pb.textContent = p ? "Play" : "Pause";
		return;
	}

	const lb = hit(e, "[data-lightbox-open], [data-lightbox-image]");
	if (lb) return void openLightbox(lb);

	const sk = hit(e, "[data-video-seek]");
	const vid = id(sk?.dataset.videoTarget);
	if (sk && vid instanceof HTMLVideoElement) {
		vid.currentTime = Number(sk.dataset.videoSeek);
		vid.play().catch(() => {});
	}
}

function setupHeader() {
	const hdr = q("[data-site-header]");
	const inner = hdr && q(".header-inner", hdr);
	const menu = id("site-menu");
	if (!hdr || !inner || !menu) return;
	const cl = hdr.classList;
	const measure = () => {
		const open = menu.matches(":popover-open");
		cl.add("is-measuring", "is-wide");
		cl.remove("is-narrow");
		sa(menu, "popover", null);
		if (inner.scrollWidth > inner.clientWidth + 1) {
			cl.replace("is-wide", "is-narrow");
			sa(menu, "popover", "auto");
			if (open) menu.showPopover?.();
		}
		requestAnimationFrame(() => cl.remove("is-measuring"));
	};
	measure();
	if ("ResizeObserver" in window) new ResizeObserver(measure).observe(hdr);
}

function setupSpy() {
	const links = qa('nav.site-nav a[href^="#"]');
	if (!links.length) return;
	const native = CSS.supports("scroll-target-group", "auto");
	const mark = (href?: string | null) => {
		for (const l of links) {
			const on = Boolean(href && l.getAttribute("href") === href);
			if (!native) l.classList.toggle("target-current", on);
			sa(l, "aria-current", String(on));
		}
	};
	if (native) {
		const sc = () =>
			mark(q("nav.site-nav a:target-current")?.getAttribute("href"));
		sc();
		doc.addEventListener("scrollend", sc);
		return;
	}
	const io = new IntersectionObserver(
		(es) => {
			const k = es.find((x) => x.isIntersecting)?.target.id;
			if (k) mark(`#${k}`);
		},
		{ rootMargin: "-45% 0px -45% 0px" },
	);
	for (const s of qa("main section[id]")) io.observe(s);
}

function setupMedia() {
	const dlg = id("media-lightbox");
	if (dlg instanceof HTMLDialogElement) {
		if (!("closedBy" in HTMLDialogElement.prototype)) {
			dlg.addEventListener("click", (e) => {
				const r = dlg.getBoundingClientRect();
				if (
					e.clientX < r.left ||
					e.clientX > r.right ||
					e.clientY < r.top ||
					e.clientY > r.bottom
				)
					dlg.close();
			});
		}
		dlg.addEventListener("close", () =>
			q("[data-lightbox-stage]", dlg)?.replaceChildren(),
		);
	}

	const vid = q("video");
	if (vid instanceof HTMLVideoElement) {
		vid.addEventListener("timeupdate", () => {
			const t = vid.currentTime;
			for (const n of qa("[data-start-sec]")) {
				const d = n.dataset;
				const e = Number(d.endSec);
				sa(
					n,
					"data-active",
					t >= Number(d.startSec) && (t < e || e >= 165) ? "true" : null,
				);
			}
		});
	}

	const search = q("[data-transcript-filter]");
	if (!(search instanceof HTMLInputElement)) return;
	const rows = qa("[data-chapter-row]");
	const cnt = q("[data-transcript-count]");
	search.addEventListener("input", () => {
		const term = search.value.trim().toLowerCase();
		for (const r of rows)
			r.hidden = Boolean(term && !r.textContent?.toLowerCase().includes(term));
		if (cnt)
			cnt.textContent = `Showing ${rows.filter((r) => !r.hidden).length} of ${rows.length} chapters`;
	});
}

/** Hydrate interactive controls, tabs, dialogs, video sync, and scrollspy. */
export function hydrateSite(): void {
	doc.addEventListener("click", handleClick);
	doc.addEventListener("keydown", (e) => {
		const tc = tabCtx(hit(e, TAB));
		if (tc) {
			const tabs = qa(TAB, tc[1]);
			const i = tabs.indexOf(tc[0]);
			const dir = ["ArrowRight", "ArrowLeft", "Home", "End"].indexOf(e.key);
			const next =
				dir < 0
					? undefined
					: tabs.at([(i + 1) % tabs.length, i - 1, 0, -1][dir] ?? 0);
			if (next) {
				e.preventDefault();
				activateTab(tc[1], next, true);
			}
		} else if (
			(e.key === " " || e.key === "Enter") &&
			hit(e, "[data-gate-select]")
		) {
			e.preventDefault();
			handleClick(e);
		}
	});
	doc.addEventListener("beforematch", (e) => {
		const p = hit(e, '[role="tabpanel"]');
		const tc = tabCtx(p && id(p.getAttribute("aria-labelledby")));
		if (tc) activateTab(tc[1], tc[0], false);
	});
	setupHeader();
	setupSpy();
	setupMedia();
}

if (doc.readyState === "loading")
	doc.addEventListener("DOMContentLoaded", hydrateSite);
else hydrateSite();
