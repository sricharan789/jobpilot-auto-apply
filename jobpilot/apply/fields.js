// Runs inside the application page. Finds every fillable field, tags it with a
// data-jp attribute so Python can find it again, and returns a description of each.
() => {
  const clean = (t) => (t || "").replace(/\s+/g, " ").trim();
  const unstar = (t) => (t || "").replace(/\*/g, "").replace(/\s+/g, " ").trim();
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return (r.width > 0 || r.height > 0) && s.visibility !== "hidden" && s.display !== "none";
  };
  const labelOf = (el) => {
    if (el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l && clean(l.innerText)) return clean(l.innerText);
    }
    const lb = el.getAttribute("aria-labelledby");
    if (lb) {
      const t = lb.split(" ").map((i) => document.getElementById(i)?.innerText || "").join(" ");
      if (clean(t)) return clean(t);
    }
    if (el.getAttribute("aria-label")) return clean(el.getAttribute("aria-label"));
    const wrap = el.closest("label");
    if (wrap && clean(wrap.innerText)) return clean(wrap.innerText);
    let c = el.parentElement;
    for (let i = 0; i < 5 && c; i++, c = c.parentElement) {
      const l = c.querySelector("label, legend, .label, .application-label, [class*='label'], [class*='Label']");
      if (l && clean(l.innerText)) return clean(l.innerText);
    }
    return clean(el.placeholder || el.name || "");
  };
  const groupLabel = (el) => {
    const fs = el.closest("fieldset");
    if (fs) {
      const lg = fs.querySelector("legend");
      if (lg && clean(lg.innerText)) return clean(lg.innerText);
    }
    let c = el.parentElement;
    for (let i = 0; i < 6 && c; i++, c = c.parentElement) {
      const others = Array.from(c.querySelectorAll("input, textarea, select")).some((x) => x.name !== el.name);
      if (others) break;
      const l = c.querySelector("label:not(:has(input)), legend, .label, [class*='question'], [class*='label']");
      if (l && clean(l.innerText)) return clean(l.innerText);
    }
    return "";
  };
  const required = (el, label) =>
    el.required || el.getAttribute("aria-required") === "true" || /\*/.test(label || "");

  const out = [];
  const groups = {};
  let n = 0;
  document.querySelectorAll("input, textarea, select").forEach((el) => {
    const tag = el.tagName.toLowerCase();
    let kind = tag === "input" ? (el.type || "text").toLowerCase() : tag;
    if (["hidden", "submit", "button", "search", "image", "reset"].includes(kind)) return;
    if (kind !== "file" && !visible(el)) return;
    if (el.closest("[aria-hidden='true']") && kind !== "file") return;

    if (kind === "radio" || kind === "checkbox") {
      const gname = el.name || el.id || `anon${n}`;
      const optLabel = labelOf(el);
      el.setAttribute("data-jp-opt", `o${n++}`);
      if (!groups[gname]) {
        const rawLabel = groupLabel(el);
        groups[gname] = {
          ref: `g${n++}`, kind, label: unstar(rawLabel), options: [], optRefs: [],
          required: required(el, rawLabel), multiple: kind === "checkbox",
        };
        out.push(groups[gname]);
      }
      groups[gname].options.push(unstar(optLabel));
      groups[gname].optRefs.push(el.getAttribute("data-jp-opt"));
      return;
    }
    const ref = `f${n++}`;
    el.setAttribute("data-jp", ref);
    if (el.getAttribute("role") === "combobox" && tag === "input") kind = "combobox";
    const rawLabel = labelOf(el);
    const field = { ref, kind, label: unstar(rawLabel), required: required(el, rawLabel), value: el.value || "" };
    if (tag === "select") {
      field.options = Array.from(el.options).map((o) => clean(o.text)).filter((t) => t && !/^select/i.test(t));
    }
    if (kind === "file") field.accept = el.accept || "";
    out.push(field);
  });
  // Single checkboxes (e.g. "I agree to the privacy policy") become yes/no fields
  for (const g of out) {
    if (g.kind === "checkbox" && g.options.length === 1) {
      g.single = true;
      if (!g.label) g.label = g.options[0];
    }
  }
  return out;
}
