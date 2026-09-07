(() => {
  const dropzone = document.getElementById("dropzone");
  const dropzoneEmpty = document.getElementById("dropzoneEmpty");
  const dropzonePreview = document.getElementById("dropzonePreview");
  const sourceImg = document.getElementById("sourceImg");
  const fileInput = document.getElementById("fileInput");
  const cameraInput = document.getElementById("cameraInput");

  const btnBrowse = document.getElementById("btnBrowse");
  const btnCamera = document.getElementById("btnCamera");
  const btnClear = document.getElementById("btnClear");
  const btnScan = document.getElementById("btnScan");
  const btnAgain = document.getElementById("btnAgain");
  const btnDownload = document.getElementById("btnDownload");
  const btnSavePhotos = document.getElementById("btnSavePhotos");

  const stageResult = document.getElementById("stage-result");
  const resultFrame = document.getElementById("resultFrame");
  const resultImg = document.getElementById("resultImg");
  const resultNote = document.getElementById("resultNote");
  const scanline = document.getElementById("scanline");
  const errorBox = document.getElementById("errorBox");

  const stageSession = document.getElementById("stage-session");
  const pagesStrip = document.getElementById("pagesStrip");
  const pageCount = document.getElementById("pageCount");
  const btnExportPdf = document.getElementById("btnExportPdf");
  const btnSaveAllPhotos = document.getElementById("btnSaveAllPhotos");
  const shareHint = document.getElementById("shareHint");

  let currentFile = null;
  let lastResult = null; // { token, downloadUrl }
  const pages = []; // { token, downloadUrl }

  const canShareFiles = !!(navigator.canShare && navigator.share);

  // ---------- helpers ----------

  function showError(msg) {
    errorBox.textContent = msg;
    errorBox.hidden = false;
  }

  function clearError() {
    errorBox.hidden = true;
    errorBox.textContent = "";
  }

  async function urlToFile(url, filename) {
    const res = await fetch(url);
    const blob = await res.blob();
    return new File([blob], filename, { type: blob.type || "image/jpeg" });
  }

  async function shareFiles(files, title) {
    const shareData = { files, title: title || "Scan" };
    if (navigator.canShare && navigator.canShare(shareData)) {
      await navigator.share(shareData);
      return true;
    }
    return false;
  }

  function downloadUrl(url, filename) {
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  // ---------- input handling ----------

  function setFile(file) {
    if (!file || !file.type.startsWith("image/")) {
      showError("Bitte eine Bilddatei auswählen.");
      return;
    }
    clearError();
    currentFile = file;
    const url = URL.createObjectURL(file);
    sourceImg.src = url;
    dropzoneEmpty.hidden = true;
    dropzonePreview.hidden = false;
    btnScan.disabled = false;
    stageResult.hidden = true;
  }

  function resetInput() {
    currentFile = null;
    sourceImg.src = "";
    dropzoneEmpty.hidden = false;
    dropzonePreview.hidden = true;
    btnScan.disabled = true;
    stageResult.hidden = true;
    fileInput.value = "";
    cameraInput.value = "";
    clearError();
  }

  btnBrowse.addEventListener("click", () => fileInput.click());
  btnCamera.addEventListener("click", () => cameraInput.click());
  btnClear.addEventListener("click", resetInput);
  btnAgain.addEventListener("click", resetInput);

  fileInput.addEventListener("change", (e) => setFile(e.target.files[0]));
  cameraInput.addEventListener("change", (e) => setFile(e.target.files[0]));

  ["dragenter", "dragover"].forEach((evt) =>
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropzone.classList.add("is-dragover");
    })
  );

  ["dragleave", "drop"].forEach((evt) =>
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropzone.classList.remove("is-dragover");
    })
  );

  dropzone.addEventListener("drop", (e) => {
    const file = e.dataTransfer.files[0];
    setFile(file);
  });

  // ---------- scanning ----------

  btnScan.addEventListener("click", async () => {
    if (!currentFile) return;
    clearError();

    const style = document.querySelector('input[name="style"]:checked').value;
    const autoCrop = document.getElementById("autoCrop").checked;
    const dewarp = document.getElementById("dewarp").checked;
    const removeFinger = document.getElementById("removeFinger").checked;

    stageResult.hidden = false;
    resultImg.hidden = true;
    btnDownload.hidden = true;
    btnSavePhotos.hidden = true;
    resultNote.textContent = "";
    scanline.classList.add("is-active");
    btnScan.disabled = true;
    stageResult.scrollIntoView({ behavior: "smooth", block: "nearest" });

    const formData = new FormData();
    formData.append("image", currentFile);
    formData.append("style", style);
    formData.append("auto_crop", autoCrop ? "true" : "false");
    formData.append("dewarp", dewarp ? "true" : "false");
    formData.append("remove_finger", removeFinger ? "true" : "false");

    try {
      const res = await fetch("/api/scan", { method: "POST", body: formData });
      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.error || "Scan fehlgeschlagen.");
      }

      resultImg.src = `${data.preview_url}?t=${Date.now()}`;
      resultImg.hidden = false;
      btnDownload.href = data.download_url;
      btnDownload.hidden = false;
      btnSavePhotos.hidden = false;

      const notes = [];
      notes.push(
        data.cropped
          ? "Papierkanten erkannt und geradegezogen."
          : "Keine eindeutigen Papierkanten gefunden — das ganze Bild wurde aufbereitet."
      );
      if (data.finger_removed) {
        notes.push("Finger/Hand am Rand wurde entfernt.");
      }
      resultNote.textContent = notes.join(" ");

      lastResult = { token: data.token, downloadUrl: data.download_url };
      addPageToSession(lastResult);
    } catch (err) {
      showError(err.message || "Etwas ist schiefgelaufen. Bitte erneut versuchen.");
      stageResult.hidden = true;
    } finally {
      scanline.classList.remove("is-active");
      btnScan.disabled = false;
    }
  });

  // ---------- save single scan to Photos ----------

  btnSavePhotos.addEventListener("click", async () => {
    if (!lastResult) return;
    try {
      const file = await urlToFile(lastResult.downloadUrl, "scan.jpg");
      const shared = await shareFiles([file], "Scan");
      if (!shared) {
        downloadUrl(lastResult.downloadUrl, "scan.jpg");
      }
    } catch (err) {
      if (err.name !== "AbortError") {
        downloadUrl(lastResult.downloadUrl, "scan.jpg");
      }
    }
  });

  // ---------- multi-page session ----------

  function addPageToSession(page) {
    pages.push(page);
    renderPages();
    stageSession.hidden = false;
    shareHint.hidden = canShareFiles;
    stageSession.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function renderPages() {
    pageCount.textContent = pages.length;
    pagesStrip.innerHTML = "";
    pages.forEach((page, idx) => {
      const wrap = document.createElement("div");
      wrap.className = "page-thumb";

      const img = document.createElement("img");
      img.src = `${page.downloadUrl}?t=${Date.now()}`;
      img.alt = `Seite ${idx + 1}`;

      const num = document.createElement("span");
      num.className = "page-thumb__num";
      num.textContent = idx + 1;

      const removeBtn = document.createElement("button");
      removeBtn.type = "button";
      removeBtn.className = "page-thumb__remove";
      removeBtn.textContent = "×";
      removeBtn.setAttribute("aria-label", "Seite entfernen");
      removeBtn.addEventListener("click", () => {
        pages.splice(idx, 1);
        renderPages();
        if (pages.length === 0) stageSession.hidden = true;
      });

      wrap.appendChild(img);
      wrap.appendChild(num);
      wrap.appendChild(removeBtn);
      pagesStrip.appendChild(wrap);
    });
  }

  btnExportPdf.addEventListener("click", async () => {
    if (pages.length === 0) return;
    clearError();
    btnExportPdf.disabled = true;
    try {
      const res = await fetch("/api/combine-pdf", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tokens: pages.map((p) => p.token) }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "PDF-Export fehlgeschlagen.");

      try {
        const file = await urlToFile(data.download_url, "scan.pdf");
        const shared = await shareFiles([file], "Scan PDF");
        if (!shared) downloadUrl(data.download_url, "scan.pdf");
      } catch {
        downloadUrl(data.download_url, "scan.pdf");
      }
    } catch (err) {
      showError(err.message || "PDF-Export fehlgeschlagen.");
    } finally {
      btnExportPdf.disabled = false;
    }
  });

  btnSaveAllPhotos.addEventListener("click", async () => {
    if (pages.length === 0) return;
    clearError();
    btnSaveAllPhotos.disabled = true;
    try {
      const files = await Promise.all(
        pages.map((p, i) => urlToFile(p.downloadUrl, `scan-${i + 1}.jpg`))
      );
      const shared = await shareFiles(files, "Scans");
      if (!shared) {
        // fallback: open each page's download individually
        for (const p of pages) {
          downloadUrl(p.downloadUrl, "scan.jpg");
        }
      }
    } catch (err) {
      if (err.name !== "AbortError") {
        for (const p of pages) {
          downloadUrl(p.downloadUrl, "scan.jpg");
        }
      }
    } finally {
      btnSaveAllPhotos.disabled = false;
    }
  });
})();
