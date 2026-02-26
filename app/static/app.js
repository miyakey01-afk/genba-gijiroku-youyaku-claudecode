document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("form");
  const fileInput = document.getElementById("fileInput");
  const dropZone = document.getElementById("dropZone");
  const fileList = document.getElementById("fileList");
  const submitBtn = document.getElementById("submitBtn");
  const processing = document.getElementById("processing");
  const statusMessage = document.getElementById("statusMessage");
  const resultSection = document.getElementById("resultSection");
  const resultContent = document.getElementById("resultContent");
  const copyBtn = document.getElementById("copyBtn");
  const downloadBtn = document.getElementById("downloadBtn");
  const newBtn = document.getElementById("newBtn");

  const progressBar = document.getElementById("progressBar");
  const progressPercent = document.getElementById("progressPercent");

  let selectedFiles = [];
  let rawMarkdown = "";

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

  form.addEventListener("submit", async (e) => {
    e.preventDefault();

    const textPaste = document.getElementById("text_paste").value;
    const outputFormat = document.querySelector('input[name="output_format"]:checked').value;

    if (!textPaste.trim() && selectedFiles.length === 0) {
      alert("テキストまたはファイルを入力してください。");
      return;
    }

    // Build FormData
    const formData = new FormData();
    formData.append("text_paste", textPaste);
    formData.append("output_format", outputFormat);
    selectedFiles.forEach((f) => formData.append("files", f));

    // Show processing UI
    form.hidden = true;
    processing.hidden = false;
    resultSection.hidden = true;
    statusMessage.textContent = "処理を開始中...";
    progressBar.style.width = "0%";
    progressPercent.textContent = "0%";

    try {
      const response = await fetch("/api/generate", {
        method: "POST",
        body: formData,
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
      processing.hidden = true;
      form.hidden = false;
      alert("エラーが発生しました: " + err.message);
    }
  });

  function handleEvent(type, data) {
    switch (type) {
      case "status":
        statusMessage.textContent = data.message;
        if (data.progress !== undefined) {
          progressBar.style.width = data.progress + "%";
          progressPercent.textContent = data.progress + "%";
        }
        break;

      case "result":
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
        processing.hidden = true;
        form.hidden = false;
        alert("エラー: " + data.message);
        break;
    }
  }

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
    resultSection.hidden = true;
    form.hidden = false;
    document.getElementById("text_paste").value = "";
    selectedFiles = [];
    renderFileList();
    rawMarkdown = "";
  });
});
