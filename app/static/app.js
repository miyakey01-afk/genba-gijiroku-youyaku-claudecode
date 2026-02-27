document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("form");
  const fileInput = document.getElementById("fileInput");
  const dropZone = document.getElementById("dropZone");
  const fileList = document.getElementById("fileList");
  const submitBtn = document.getElementById("submitBtn");
  const rucasBtn = document.getElementById("rucasBtn");
  const processing = document.getElementById("processing");
  const statusMessage = document.getElementById("statusMessage");
  const resultSection = document.getElementById("resultSection");
  const resultContent = document.getElementById("resultContent");
  const copyBtn = document.getElementById("copyBtn");
  const downloadBtn = document.getElementById("downloadBtn");
  const newBtn = document.getElementById("newBtn");

  const progressBar = document.getElementById("progressBar");
  const progressPercent = document.getElementById("progressPercent");

  const recordBtn = document.getElementById("recordBtn");
  const recordingTime = document.getElementById("recordingTime");
  const recordingIndicator = document.getElementById("recordingIndicator");

  const audioRecovery = document.getElementById("audioRecovery");
  const recoveryDownloadBtn = document.getElementById("recoveryDownloadBtn");

  let selectedFiles = [];
  let rawMarkdown = "";
  let simulatedTimer = null;

  // --- Audio recording ---
  let mediaRecorder = null;
  let recordedChunks = [];
  let recordingTimer = null;
  let recordingStartTime = null;
  let lastRecordedBlob = null;
  let lastRecordedFileName = null;

  recordBtn.addEventListener("click", async () => {
    if (mediaRecorder && mediaRecorder.state === "recording") {
      // Stop recording
      mediaRecorder.stop();
      return;
    }

    // Start recording
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      recordedChunks = [];

      mediaRecorder = new MediaRecorder(stream, { mimeType: "audio/webm" });

      mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) recordedChunks.push(e.data);
      };

      mediaRecorder.onstop = () => {
        // Stop all tracks to release the microphone
        stream.getTracks().forEach((t) => t.stop());

        // Stop timer
        clearInterval(recordingTimer);
        recordingTimer = null;

        // Update UI
        recordBtn.title = "録音開始";
        recordBtn.classList.remove("recording");
        recordingTime.hidden = true;
        recordingIndicator.hidden = true;

        // Create file from recorded data
        const blob = new Blob(recordedChunks, { type: "audio/webm" });
        const now = new Date();
        const ts = now.getFullYear()
          + String(now.getMonth() + 1).padStart(2, "0")
          + String(now.getDate()).padStart(2, "0")
          + "_"
          + String(now.getHours()).padStart(2, "0")
          + String(now.getMinutes()).padStart(2, "0");
        const fileName = `録音_${ts}.webm`;
        const file = new File([blob], fileName, { type: "audio/webm" });

        // Keep blob for error recovery download
        lastRecordedBlob = blob;
        lastRecordedFileName = fileName;

        selectedFiles.push(file);
        renderFileList();
      };

      mediaRecorder.start();

      // Update UI
      recordBtn.title = "録音停止";
      recordBtn.classList.add("recording");
      recordingTime.hidden = false;
      recordingIndicator.hidden = false;
      recordingStartTime = Date.now();

      // Start elapsed time display
      updateRecordingTime();
      recordingTimer = setInterval(updateRecordingTime, 1000);

    } catch (err) {
      if (err.name === "NotAllowedError") {
        alert("マイクへのアクセスが許可されていません。ブラウザの設定を確認してください。");
      } else {
        alert("録音を開始できませんでした: " + err.message);
      }
    }
  });

  function updateRecordingTime() {
    const elapsed = Math.floor((Date.now() - recordingStartTime) / 1000);
    const min = String(Math.floor(elapsed / 60)).padStart(2, "0");
    const sec = String(elapsed % 60).padStart(2, "0");
    recordingTime.textContent = `${min}:${sec}`;
  }

  // --- Simulated progress ---
  // Gradually advances the progress bar while waiting for server events
  // (e.g. during long Gemini API calls that can take 1-4+ minutes)

  function startSimulatedProgress(fromPercent, toPercent, durationMs) {
    stopSimulatedProgress();
    const startTime = Date.now();
    const range = toPercent - fromPercent;

    simulatedTimer = setInterval(() => {
      const elapsed = Date.now() - startTime;
      const ratio = Math.min(elapsed / durationMs, 1);
      // Ease-out curve: fast at start, slows down toward the end
      const eased = 1 - Math.pow(1 - ratio, 2);
      const current = Math.round(fromPercent + range * eased);
      progressBar.style.width = current + "%";
      progressPercent.textContent = current + "%";
      if (ratio >= 1) {
        clearInterval(simulatedTimer);
        simulatedTimer = null;
      }
    }, 500);
  }

  function stopSimulatedProgress() {
    if (simulatedTimer) {
      clearInterval(simulatedTimer);
      simulatedTimer = null;
    }
  }

  // --- File handling ---

  fileInput.addEventListener("change", () => {
    addFiles(fileInput.files);
  });

  dropZone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropZone.classList.add("dragover");
  });

  dropZone.addEventListener("dragleave", () => {
    dropZone.classList.remove("dragover");
  });

  dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropZone.classList.remove("dragover");
    addFiles(e.dataTransfer.files);
  });

  function addFiles(files) {
    for (const f of files) {
      if (!selectedFiles.some((sf) => sf.name === f.name && sf.size === f.size)) {
        selectedFiles.push(f);
      }
    }
    renderFileList();
  }

  function renderFileList() {
    fileList.innerHTML = "";
    selectedFiles.forEach((f, i) => {
      const div = document.createElement("div");
      div.className = "file-item";
      const sizeMB = (f.size / 1024 / 1024).toFixed(1);
      div.innerHTML = `
        <span class="file-name">${f.name}</span>
        <span class="file-size">${sizeMB} MB</span>
        <button type="button" class="remove-file" data-index="${i}">&times;</button>
      `;
      fileList.appendChild(div);
    });

    fileList.querySelectorAll(".remove-file").forEach((btn) => {
      btn.addEventListener("click", () => {
        selectedFiles.splice(parseInt(btn.dataset.index), 1);
        renderFileList();
      });
    });
  }

  // --- Form submission ---

  const cancelBtn = document.getElementById("cancelBtn");
  let abortController = null;

  cancelBtn.addEventListener("click", () => {
    if (abortController) {
      abortController.abort();
      abortController = null;
    }
    stopSimulatedProgress();
    processing.hidden = true;
    form.hidden = false;
    showAudioRecovery();
  });

  async function submitGeneration(mode) {
    const textPaste = document.getElementById("text_paste").value;
    const outputFormat = mode === "rucas"
      ? "text"
      : document.querySelector('input[name="output_format"]:checked').value;

    if (!textPaste.trim() && selectedFiles.length === 0) {
      alert("テキストまたはファイルを入力してください。");
      return;
    }

    // Build FormData
    const formData = new FormData();
    formData.append("text_paste", textPaste);
    formData.append("output_format", outputFormat);
    formData.append("mode", mode);
    selectedFiles.forEach((f) => formData.append("files", f));

    // Show processing UI
    form.hidden = true;
    processing.hidden = false;
    resultSection.hidden = true;
    audioRecovery.hidden = true;
    statusMessage.textContent = mode === "rucas"
      ? "RUCAS営業情報を生成開始中..."
      : "処理を開始中...";
    progressBar.style.width = "0%";
    progressPercent.textContent = "0%";

    abortController = new AbortController();

    try {
      const response = await fetch("/api/generate", {
        method: "POST",
        body: formData,
        signal: abortController.signal,
      });

      if (!response.ok) {
        throw new Error(`サーバーエラー: ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        let eventType = "";
        let eventData = "";

        for (const line of lines) {
          if (line.startsWith("event: ")) {
            eventType = line.slice(7);
          } else if (line.startsWith("data: ")) {
            eventData = line.slice(6);

            if (eventType && eventData) {
              handleEvent(eventType, JSON.parse(eventData));
              eventType = "";
              eventData = "";
            }
          }
        }
      }
    } catch (err) {
      stopSimulatedProgress();
      // User-initiated cancel — go back silently with recovery bar
      if (err.name === "AbortError") {
        processing.hidden = true;
        form.hidden = false;
        showAudioRecovery();
        return;
      }
      processing.hidden = true;
      form.hidden = false;
      showAudioRecovery();
      alert("エラーが発生しました: " + err.message);
    } finally {
      abortController = null;
    }
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    submitGeneration("minutes");
  });

  rucasBtn.addEventListener("click", () => {
    submitGeneration("rucas");
  });

  function handleEvent(type, data) {
    switch (type) {
      case "status":
        stopSimulatedProgress();
        statusMessage.textContent = data.message;
        if (data.progress !== undefined) {
          progressBar.style.width = data.progress + "%";
          progressPercent.textContent = data.progress + "%";

          // When Gemini API processing starts (30%), simulate progress
          // up to 80% over 4 minutes so the user sees movement
          if (data.progress === 30) {
            startSimulatedProgress(30, 80, 240000);
          }
        }
        break;

      case "result":
        stopSimulatedProgress();
        processing.hidden = true;
        resultSection.hidden = false;
        rawMarkdown = data.markdown;
        resultContent.innerHTML = marked.parse(data.markdown);

        if (data.download_url) {
          downloadBtn.hidden = false;
          if (data.output_format === "word") {
            downloadBtn.textContent = "DOCXダウンロード";
          } else {
            downloadBtn.textContent = "TXTダウンロード";
          }
          downloadBtn.onclick = () => {
            window.location.href = data.download_url;
          };
        } else {
          downloadBtn.hidden = true;
        }
        break;

      case "error":
        stopSimulatedProgress();
        processing.hidden = true;
        form.hidden = false;
        showAudioRecovery();
        alert("エラー: " + data.message);
        break;
    }
  }

  // --- Audio recovery on error ---

  function showAudioRecovery() {
    if (lastRecordedBlob) {
      audioRecovery.hidden = false;
    }
  }

  recoveryDownloadBtn.addEventListener("click", () => {
    if (!lastRecordedBlob) return;
    const url = URL.createObjectURL(lastRecordedBlob);
    const a = document.createElement("a");
    a.href = url;
    a.download = lastRecordedFileName || "録音データ.webm";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  });

  // --- Result actions ---

  copyBtn.addEventListener("click", () => {
    navigator.clipboard.writeText(rawMarkdown).then(() => {
      copyBtn.textContent = "コピーしました";
      setTimeout(() => {
        copyBtn.textContent = "コピー";
      }, 2000);
    });
  });

  newBtn.addEventListener("click", () => {
    // Stop recording if active
    if (mediaRecorder && mediaRecorder.state === "recording") {
      mediaRecorder.stop();
    }
    resultSection.hidden = true;
    audioRecovery.hidden = true;
    form.hidden = false;
    document.getElementById("text_paste").value = "";
    selectedFiles = [];
    renderFileList();
    rawMarkdown = "";
    lastRecordedBlob = null;
    lastRecordedFileName = null;
  });
});
