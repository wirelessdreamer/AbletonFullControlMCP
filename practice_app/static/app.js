// Practice Pack — single-page app logic (Alpine.js component).
// File-system-backed setlists from /api/setlists; audio via /api/audio/...
// Track mapping = (instrument, vocalsOn, boostOn) → filename, hydrated from
// the per-song `tracks` index the backend builds by parsing filenames.

function practiceApp() {
  return {
    // --- state ----------------------------------------------------------
    setlists: [],
    playlists: [],
    currentSetlist: null,
    currentSong: null,

    // Playback config
    selectedInstrument: "drums",
    vocalsOn: false,
    boostOn: false,
    clickOn: false,
    // Click volume runs independently from the main volume so a user
    // can mix the click quieter than the backing track without going
    // to the OS mixer. Kept in [0, 1] like the primary volume.
    clickVolume: 0.6,
    playbackRate: 1.0,
    volume: 0.85,

    // Player runtime
    isPlaying: false,
    playerTime: 0,
    playerDuration: 0,

    // Playlist mode
    playlistMode: false,
    queue: [],
    queueIndex: -1,

    // Chord sheet
    sheetContent: "",
    sheetEditing: false,
    sheetDraft: "",

    // UI toast for errors
    lastError: "",
    _errTimer: null,

    // --- lifecycle ------------------------------------------------------
    async init() {
      // Attach keyboard shortcuts BEFORE any awaits so they're live ASAP.
      // Use capture phase so we run BEFORE any focused element gets to handle
      // the key (e.g., space on a focused button → browser click). Bind 'this'
      // explicitly to avoid Alpine proxy weirdness.
      const handler = this.handleKey.bind(this);
      window.addEventListener("keydown", handler, true);
      window.addEventListener("keyup", (e) => {
        // Some browsers fire button-click on keyup for Space — also suppress.
        if ((e.code === "Space" || e.code === "KeyK") && !this._inTextField(e.target)) {
          e.preventDefault();
        }
      }, true);
      await this.reloadSetlists();
      await this.loadPlaylists();
      // Watch playback-config changes; swap audio src when relevant
      this.$watch("selectedInstrument", () => this.updateAudio());
      this.$watch("vocalsOn", () => this.updateAudio());
      this.$watch("boostOn", () => this.updateAudio());
      // Click track: swap-in when toggled; keep in sync with primary.
      this.$watch("clickOn", () => this.updateClick());
      this.$watch("clickVolume", () => this.setClickVolume());
    },

    _inTextField(t) {
      const tag = (t?.tagName || "").toUpperCase();
      return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || !!t?.isContentEditable;
    },

    // Keyboard shortcuts. Space/K = play-pause. ←/→ = seek 5s. ↑/↓ = vol 5%.
    // J/L = seek 10s. M = mute. N = next in playlist queue.
    handleKey(e) {
      const t = e.target;
      if (this._inTextField(t)) return;
      console.log("[key]", e.code, "target=", (t?.tagName || "").toUpperCase());
      switch (e.code) {
        case "Space":
        case "KeyK":
          // CRITICAL ORDER: preventDefault + stopImmediatePropagation must
          // run BEFORE we call togglePlay, otherwise a focused button can
          // also fire its click and double-toggle.
          e.preventDefault();
          e.stopImmediatePropagation();
          if (t?.blur) t.blur();
          this.togglePlay();
          break;
        case "ArrowRight":
          e.preventDefault();
          this.seek(this.playerTime + 5);
          break;
        case "ArrowLeft":
          e.preventDefault();
          this.seek(Math.max(0, this.playerTime - 5));
          break;
        case "KeyL":
          e.preventDefault();
          this.seek(this.playerTime + 10);
          break;
        case "KeyJ":
          e.preventDefault();
          this.seek(Math.max(0, this.playerTime - 10));
          break;
        case "ArrowUp":
          e.preventDefault();
          this.volume = Math.min(1, this.volume + 0.05);
          this.setVolume();
          break;
        case "ArrowDown":
          e.preventDefault();
          this.volume = Math.max(0, this.volume - 0.05);
          this.setVolume();
          break;
        case "KeyM":
          e.preventDefault();
          if (this.$refs.audio) this.$refs.audio.muted = !this.$refs.audio.muted;
          break;
        case "KeyN":
          if (this.playlistMode) { e.preventDefault(); this.next(); }
          break;
      }
    },

    async reloadSetlists() {
      try {
        const r = await fetch("/api/setlists");
        this.setlists = await r.json();
      } catch (err) {
        console.error("Failed to load setlists:", err);
        this.setlists = [];
      }
    },

    async loadPlaylists() {
      try {
        const r = await fetch("/api/playlists");
        this.playlists = await r.json();
      } catch (err) {
        this.playlists = [];
      }
    },

    // --- status helpers -----------------------------------------------
    statusDotClass(status) {
      switch (status) {
        case "ready":           return "bg-emerald-500";
        case "needs_review":    return "bg-warm-500 animate-pulse";
        case "needs_transpose": return "bg-accent-500";
        case "needs_audio":     return "bg-gray-600";
        case "partial":         return "bg-warm-500/60";
        default:                return "bg-gray-500";
      }
    },

    statusLabel(status) {
      return ({
        ready: "Ready",
        needs_review: "Needs review — key uncertain",
        needs_transpose: "Needs transpose",
        needs_audio: "Needs audio",
        partial: "Partial pack",
      })[status] || status || "Unknown";
    },

    async resolveReview(chosenKey) {
      if (!this.currentSong || !this.currentSetlist) return;
      if (!confirm(`Set source key to ${chosenKey}? This recomputes the shift and flips the song's status.`)) return;
      try {
        const r = await fetch(`/api/resolve/${encodeURIComponent(this.currentSetlist.id)}/${encodeURIComponent(this.currentSong.id)}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ chosen_key: chosenKey }),
        });
        if (!r.ok) throw new Error(await r.text());
        const data = await r.json();
        this.flashError("");  // clear any prior toast
        // Refresh setlist data to pick up the new status
        await this.reloadSetlists();
        // Re-find the song in the refreshed setlist data
        const sl = this.setlists.find(s => s.id === this.currentSetlist.id);
        if (sl) {
          this.currentSetlist = sl;
          const song = sl.songs.find(s => s.id === this.currentSong.id);
          if (song) this.currentSong = song;
        }
        alert(`Source key set to ${chosenKey}. New status: ${data.new_status}, shift ${data.shift_semitones >= 0 ? '+' : ''}${data.shift_semitones} semitones.`);
      } catch (err) {
        this.flashError(`Resolve failed: ${err.message}`);
      }
    },

    resolveReviewCustom() {
      const k = prompt("Enter source key (e.g. C, F#, Bb):");
      if (k) this.resolveReview(k.trim());
    },

    // --- selection ------------------------------------------------------
    selectSetlist(sl) {
      this.currentSetlist = sl;
      if (sl.songs.length > 0 && !this.currentSong) this.selectSong(sl.songs[0]);
    },

    async selectSong(song) {
      this.currentSong = song;
      // Pick a sensible default instrument from what's available
      const avail = this.availableInstruments();
      if (avail.length > 0 && !avail.includes(this.selectedInstrument)) {
        this.selectedInstrument = avail.includes("drums") ? "drums" : avail[0];
      }
      // If the incoming song doesn't have a click.wav, force the toggle
      // off — leaving it "on" but silent would be confusing UI state.
      if (!song?.has_click && this.clickOn) this.clickOn = false;
      // Load chord sheet
      await this.loadSheet();
      this.updateAudio();
      this.updateClick();
    },

    // --- track resolution ---------------------------------------------
    availableInstruments() {
      return this.currentSong?.instruments_available || [];
    },

    currentTrackKey() {
      // Returns the key into song.tracks based on current config
      if (!this.boostOn) return this.vocalsOn ? "full_mix" : "no_vocals";
      return `${this.selectedInstrument}_${this.vocalsOn ? "with_vocals" : "no_vocals"}`;
    },

    currentTrackFilename() {
      const key = this.currentTrackKey();
      return this.currentSong?.tracks?.[key];
    },

    currentTrackUrl() {
      const fn = this.currentTrackFilename();
      if (!fn || !this.currentSong || !this.currentSetlist) return null;
      return `/api/audio/${encodeURIComponent(this.currentSetlist.id)}/${encodeURIComponent(this.currentSong.id)}/${encodeURIComponent(fn)}`;
    },

    // --- click track ---------------------------------------------------
    // Click plays via a SECOND <audio> element ($refs.click) that mirrors
    // the primary player's transport (play/pause/seek/rate). Volume runs
    // independently so the click can be quieter than the backing track.
    // When the current song has no click.wav (has_click=false), the
    // toggle is disabled and updateClick() is a no-op.

    clickTrackUrl() {
      if (!this.currentSong?.has_click) return null;
      const fn = this.currentSong.tracks?.click;
      if (!fn || !this.currentSetlist) return null;
      return `/api/audio/${encodeURIComponent(this.currentSetlist.id)}/${encodeURIComponent(this.currentSong.id)}/${encodeURIComponent(fn)}`;
    },

    updateClick() {
      const click = this.$refs.click;
      if (!click) return;
      if (!this.clickOn) {
        // Toggle off — stop the click without unloading. Keeping the
        // buffer around means re-enabling doesn't re-download.
        click.pause();
        return;
      }
      const url = this.clickTrackUrl();
      if (!url) {
        click.pause();
        click.removeAttribute("src");
        click.load();
        return;
      }
      const fullUrl = new URL(url, window.location.origin).href;
      const primary = this.$refs.audio;
      if (click.src !== fullUrl) {
        click.src = url;
        click.load();
      }
      click.playbackRate = this.playbackRate;
      click.volume = this.clickVolume;
      // Try to align to the primary's current position + playing state.
      // Seek + play happen after enough data is buffered.
      const target = () => {
        if (primary && !isNaN(primary.currentTime)) {
          click.currentTime = primary.currentTime;
        }
        if (primary && !primary.paused) {
          click.play().catch(err => console.warn("[click] play failed:", err));
        }
      };
      if (click.readyState >= 2) {
        target();
      } else {
        click.addEventListener("canplay", target, { once: true });
      }
    },

    setClickVolume() {
      if (this.$refs.click) this.$refs.click.volume = this.clickVolume;
    },

    updateAudio() {
      const audio = this.$refs.audio;
      if (!audio) { console.warn("[player] updateAudio: $refs.audio missing"); return; }
      const url = this.currentTrackUrl();
      if (!url) {
        console.warn("[player] updateAudio: no track URL for current config", {
          song: this.currentSong?.title,
          trackKey: this.currentTrackKey(),
          availableTrackKeys: Object.keys(this.currentSong?.tracks || {}),
        });
        audio.pause();
        audio.removeAttribute("src");
        audio.load();
        return;
      }
      // Preserve playback position if just swapping variants (same song)
      const wasPlaying = !audio.paused;
      const prevTime = audio.currentTime;
      // audio.src is the resolved absolute URL — compare via endsWith on the path
      const fullUrl = new URL(url, window.location.origin).href;
      if (audio.src === fullUrl) return;  // no change
      console.log("[player] loading:", url);
      audio.src = url;
      audio.load();
      audio.addEventListener("loadedmetadata", () => {
        if (!isNaN(prevTime) && prevTime > 0 && prevTime < audio.duration) {
          audio.currentTime = prevTime;
        }
        if (wasPlaying) audio.play().catch(err => console.error("[player] resume failed:", err));
      }, { once: true });
      audio.playbackRate = this.playbackRate;
      audio.volume = this.volume;
    },

    // --- playback controls --------------------------------------------
    togglePlay() {
      const audio = this.$refs.audio;
      if (!audio) { console.error("[player] no audio element"); return; }
      // If src isn't set yet (race: user clicked before updateAudio ran), force it now
      if (!audio.src || audio.src === window.location.href) {
        console.warn("[player] no src — forcing updateAudio before play");
        this.updateAudio();
        if (!audio.src || audio.src === window.location.href) {
          this.flashError("No track loaded — pick an instrument or check that practice-pack files exist for this song.");
          return;
        }
      }
      if (audio.paused) {
        audio.play()
          .then(() => {
            console.log("[player] playing:", audio.src);
            // Mirror to click when enabled.
            this._playClickInSync();
          })
          .catch(err => {
            console.error("[player] play() rejected:", err);
            this.flashError(`Playback failed: ${err.name} — ${err.message}`);
          });
      } else {
        audio.pause();
        if (this.$refs.click) this.$refs.click.pause();
      }
    },

    // Sync helper — used when starting playback from any entry point.
    _playClickInSync() {
      const click = this.$refs.click;
      const audio = this.$refs.audio;
      if (!click || !audio || !this.clickOn) return;
      // Match position + rate + volume before firing play so the two
      // don't drift out of sync in the first few frames.
      click.playbackRate = this.playbackRate;
      click.volume = this.clickVolume;
      if (!isNaN(audio.currentTime)) click.currentTime = audio.currentTime;
      click.play().catch(err => console.warn("[click] play failed:", err));
    },

    flashError(msg) {
      this.lastError = msg;
      clearTimeout(this._errTimer);
      this._errTimer = setTimeout(() => { this.lastError = ""; }, 6000);
    },

    seek(t) {
      const audio = this.$refs.audio;
      if (!audio) return;
      audio.currentTime = Number(t);
      // Keep the click in lockstep with the primary track.
      if (this.$refs.click && this.clickOn) {
        this.$refs.click.currentTime = Number(t);
      }
    },

    setRate() {
      if (this.$refs.audio) this.$refs.audio.playbackRate = this.playbackRate;
      // Rate affects the click too so beats stay aligned with the music.
      if (this.$refs.click) this.$refs.click.playbackRate = this.playbackRate;
    },

    setVolume() {
      if (this.$refs.audio) this.$refs.audio.volume = this.volume;
    },

    formatTime(s) {
      if (!s || isNaN(s)) return "0:00";
      const m = Math.floor(s / 60);
      const sec = Math.floor(s % 60);
      return `${m}:${sec.toString().padStart(2, "0")}`;
    },

    // --- chord sheet --------------------------------------------------
    async loadSheet() {
      if (!this.currentSong || !this.currentSetlist) { this.sheetContent = ""; return; }
      try {
        const r = await fetch(`/api/sheet/${encodeURIComponent(this.currentSetlist.id)}/${encodeURIComponent(this.currentSong.id)}`);
        const data = await r.json();
        this.sheetContent = data.content || "";
        this.sheetEditing = false;
      } catch (err) {
        this.sheetContent = "";
      }
    },

    async saveSheet() {
      if (!this.currentSong || !this.currentSetlist) return;
      try {
        await fetch(`/api/sheet/${encodeURIComponent(this.currentSetlist.id)}/${encodeURIComponent(this.currentSong.id)}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ filename: "chords.pro", content: this.sheetDraft }),
        });
        this.sheetContent = this.sheetDraft;
        this.sheetEditing = false;
      } catch (err) {
        alert("Failed to save sheet: " + err.message);
      }
    },

    renderSheet(text) {
      if (!text) return "";
      const escape = (s) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
      return text.split("\n").map((line) => {
        // ChordPro directive: {title: X} / {key: G} / {verse: 1}
        const dirMatch = line.match(/^\{(\w+)\s*:?\s*(.*?)\}$/);
        if (dirMatch) {
          const key = dirMatch[1].toLowerCase();
          const val = dirMatch[2];
          if (key === "title") return `<div class="text-xl font-semibold text-white mt-2">${escape(val)}</div>`;
          if (key === "key") return `<div class="text-xs text-warm-400 font-mono mb-2">key: ${escape(val)}</div>`;
          if (["verse", "chorus", "bridge", "tag", "intro", "outro", "pre-chorus", "prechorus", "interlude"].includes(key)) {
            return `<div class="text-warm-400 font-semibold uppercase tracking-wider text-xs mt-4 mb-1">${escape(key)}${val ? " " + escape(val) : ""}</div>`;
          }
          return `<div class="text-gray-500 italic text-xs">${escape(line)}</div>`;
        }
        // Comment-style line break
        if (line.trim() === "") return `<div class="h-3"></div>`;
        // Section header via #
        if (/^#+\s/.test(line)) {
          return `<div class="text-warm-400 font-semibold uppercase tracking-wider text-xs mt-4 mb-1">${escape(line.replace(/^#+\s*/, ""))}</div>`;
        }
        // Plain text line, no chords
        if (!line.includes("[")) return `<div class="text-gray-200">${escape(line)}</div>`;

        // ChordPro inline chords: render chord row + lyric row
        let chordRow = "";
        let lyricRow = "";
        let i = 0;
        while (i < line.length) {
          if (line[i] === "[") {
            const end = line.indexOf("]", i);
            if (end === -1) { lyricRow += line[i]; i++; continue; }
            const chord = line.slice(i + 1, end);
            // Pad chord row with spaces up to the lyric position
            while (chordRow.length < lyricRow.length) chordRow += " ";
            chordRow += chord;
            i = end + 1;
          } else {
            lyricRow += line[i];
            i++;
          }
        }
        // Pad lyric row to chord row length so they align
        const maxLen = Math.max(chordRow.length, lyricRow.length);
        chordRow = chordRow.padEnd(maxLen, " ");
        lyricRow = lyricRow.padEnd(maxLen, " ");
        return `<div class="text-accent-400 font-medium whitespace-pre">${escape(chordRow)}</div>` +
               `<div class="text-gray-200 whitespace-pre mb-1">${escape(lyricRow)}</div>`;
      }).join("");
    },

    // --- playlist / queue --------------------------------------------
    addCurrentToQueue() {
      if (!this.currentSong || !this.currentSetlist) return;
      this.queue.push({
        setlistId: this.currentSetlist.id,
        setlistName: this.currentSetlist.name,
        songId: this.currentSong.id,
        title: this.currentSong.title,
        targetKey: this.currentSong.target_key,
        instrument: this.boostOn ? this.selectedInstrument : null,
        vocalsOn: this.vocalsOn,
        boostOn: this.boostOn,
      });
    },

    removeFromQueue(idx) {
      this.queue.splice(idx, 1);
      if (idx === this.queueIndex) { this.queueIndex = -1; }
      else if (idx < this.queueIndex) this.queueIndex--;
    },

    playFromQueue(idx) {
      const item = this.queue[idx];
      if (!item) return;
      this.queueIndex = idx;
      const sl = this.setlists.find(s => s.id === item.setlistId);
      if (!sl) return;
      const song = sl.songs.find(s => s.id === item.songId);
      if (!song) return;
      this.currentSetlist = sl;
      this.selectedInstrument = item.instrument || this.selectedInstrument;
      this.vocalsOn = item.vocalsOn;
      this.boostOn = item.boostOn;
      this.selectSong(song);
      // Auto-play after src loads
      this.$nextTick(() => {
        const audio = this.$refs.audio;
        if (audio) audio.addEventListener("canplay", () => audio.play().catch(() => {}), { once: true });
      });
    },

    onTrackEnded() {
      if (!this.playlistMode) return;
      if (this.queueIndex + 1 < this.queue.length) this.playFromQueue(this.queueIndex + 1);
    },

    next() {
      if (this.queueIndex + 1 < this.queue.length) this.playFromQueue(this.queueIndex + 1);
    },

    clearQueue() {
      this.queue = [];
      this.queueIndex = -1;
    },

    async savePlaylistPrompt() {
      const name = prompt("Playlist name:");
      if (!name) return;
      const id = name.toLowerCase().replace(/[^a-z0-9_\-]+/g, "_");
      const body = { name, items: this.queue };
      const r = await fetch(`/api/playlists/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (r.ok) {
        await this.loadPlaylists();
        alert(`Saved as "${name}".`);
      } else {
        alert("Save failed.");
      }
    },

    loadPlaylist(pl) {
      this.queue = pl.items || [];
      this.queueIndex = -1;
      this.playlistMode = true;
      if (this.queue.length > 0) this.playFromQueue(0);
    },
  };
}
