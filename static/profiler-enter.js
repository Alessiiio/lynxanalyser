/** Firmbar entry → Profiler full page (Admin). */
(function () {
  function isAdminUser() {
    return window.__lynxUser?.role === "admin";
  }

  function profilerUrlForCompany(company) {
    const qs = new URLSearchParams();
    if (company?.name) qs.set("company", company.name);
    if (company?.uid) qs.set("uid", company.uid);
    return qs.toString() ? `/profiler?${qs}` : "/profiler";
  }

  window.refreshProfilerAdminUi = function refreshProfilerAdminUi() {
    document.querySelectorAll("#profilerEnterBtn, .ca-profiler-enter").forEach((el) => {
      el.classList.toggle("hidden", !isAdminUser());
    });
  };

  window.openProfiler = function openProfiler() {
    if (!isAdminUser()) return;
    const company =
      typeof currentCompany !== "undefined" && currentCompany
        ? currentCompany
        : {
            name: document.getElementById("companyInput")?.value || "",
            uid: (typeof currentCompany !== "undefined" && currentCompany?.uid) || "",
          };
    location.href = profilerUrlForCompany(company);
  };

  const prevReady = window.onLynxUserReady;
  window.onLynxUserReady = function onLynxUserReady(u) {
    if (typeof prevReady === "function") prevReady(u);
    window.refreshProfilerAdminUi();
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => window.refreshProfilerAdminUi());
  } else {
    window.refreshProfilerAdminUi();
  }
})();
