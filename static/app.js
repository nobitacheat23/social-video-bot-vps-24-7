// State Management
let currentPlatform = 'threads';
let isDarkMode = false;
let currentPost = null;
let currentComments = [];

// Initialize default preset on load
window.addEventListener('DOMContentLoaded', async () => {
  loadPreset('motvongthread');
  await refreshBotStatus(true);
});

function setTemplate(platform) {
  currentPlatform = platform;
  ['threads', 'tiktok', 'facebook'].forEach(p => {
    const btn = document.getElementById(`btnTpl${p.charAt(0).toUpperCase() + p.slice(1)}`);
    if (btn) {
      if (p === platform) {
        btn.className = "py-2 px-3 rounded-xl border text-xs font-semibold transition border-indigo-500 bg-indigo-500/10 text-white";
      } else {
        btn.className = "py-2 px-3 rounded-xl border border-gray-700 bg-gray-900 text-gray-400 hover:text-white text-xs font-semibold transition";
      }
    }
  });
}

function setDarkMode(dark) {
  isDarkMode = dark;
  const btnDark = document.getElementById('btnDark');
  const btnLight = document.getElementById('btnLight');
  if (dark) {
    btnDark.className = "px-2.5 py-1 rounded bg-indigo-600 text-white font-medium";
    btnLight.className = "px-2.5 py-1 rounded text-gray-400 hover:text-white font-medium";
  } else {
    btnLight.className = "px-2.5 py-1 rounded bg-indigo-600 text-white font-medium";
    btnDark.className = "px-2.5 py-1 rounded text-gray-400 hover:text-white font-medium";
  }
}

function updateRateLabel(val) {
  const prefix = val > 0 ? `+${val}%` : `${val}%`;
  document.getElementById('rateLabel').innerText = prefix;
}

async function loadPreset(presetKey) {
  if (presetKey === 'motvongthread') {
    setTemplate('threads');
    setDarkMode(false); // Native crisp white Threads cap like @Vietcaigido? reference video
    document.getElementById('urlInput').value = 'https://www.threads.net/@_tr.tvy/post/sample';
    document.getElementById('runningTextInput').value = '@Vietcaigido?';
    document.getElementById('runningTextMode').value = 'top_left';
    
    const bgSelect = document.getElementById('bgSelect');
    if (bgSelect) bgSelect.value = 'minecraft_parkour.mp4';

    const bgmSelect = document.getElementById('bgmSelect');
    if (bgmSelect) bgmSelect.value = 'meme_tiktok_viral.wav';

    const chkBadge = document.getElementById('chkBadge');
    if (chkBadge) chkBadge.checked = false;
    
    currentPost = {
      id: 'threads_vietcaigido_demo',
      username: 'ens_dahy',
      handle: 'ens_dahy',
      avatar_url: 'https://api.dicebear.com/7.x/avataaars/svg?seed=ens_dahy',
      content: 'thề VN mình có tỉ quả joke hài vãi, làm ơn đừng biến mất nhé tr=))))))',
      likes: '32,1K',
      replies: 506,
      reposts: '5,2K',
      shares: '1,2K',
      time_ago: '',
      is_op: true
    };
    currentComments = [
      {
        id: 'c1',
        username: '_tr.tvy',
        handle: '_tr.tvy',
        avatar_url: 'https://api.dicebear.com/7.x/avataaars/svg?seed=trtvy',
        image_url: '/assets/demo/pod_cau_meme.png',
        content: 'Pod thủ vào xác nhận hộ t: bạn t có pod vị cau đó',
        likes: '8,5K',
        likes_count: 8500,
        replies: 397,
        reposts: '938',
        shares: '1,9K',
        time_ago: '',
        selected: true,
        meme_reaction: 'none',
        sfx_reaction: 'vine_boom.wav'
      },
      {
        id: 'c2',
        username: 'salamanca.jnr',
        handle: 'salamanca.jnr',
        avatar_url: 'https://api.dicebear.com/7.x/avataaars/svg?seed=salamanca',
        content: 'mượn tý ba t dạo này răng yếu',
        likes: '3K',
        likes_count: 3000,
        replies: 39,
        reposts: '193',
        shares: '323',
        time_ago: '',
        selected: true,
        meme_reaction: 'none',
        sfx_reaction: 'cartoon_laugh.wav'
      },
      {
        id: 'c3',
        username: 'feell1kethunder',
        handle: 'feell1kethunder',
        avatar_url: 'https://api.dicebear.com/7.x/avataaars/svg?seed=thunder',
        content: 'có vậy thôi mà nằm cười bữa giờ đó =))) "Rốt cuộc năm đó Chu Hải Yến đã nói gì vào bên tai bị điếc của Đường Hà Thanh?" — "nhả vía hết điếc"',
        likes: '5,9K',
        likes_count: 5900,
        replies: 34,
        reposts: '374',
        shares: '361',
        time_ago: '',
        selected: true,
        meme_reaction: 'none',
        sfx_reaction: 'bruh.wav'
      },
      {
        id: 'c4',
        username: 'an_speakin',
        handle: 'an_speakin',
        avatar_url: 'https://api.dicebear.com/7.x/avataaars/svg?seed=anspeakin',
        content: 'ê mấy thằng nghiện tìm chỗ mua đồ cấm ở đâu nhỉ? kiểu làm sao để liên lạc đc vs dealer vậy? câu hỏi giả định thui nha',
        likes: '4,2K',
        likes_count: 4200,
        replies: 455,
        reposts: '120',
        shares: '89',
        time_ago: '',
        selected: true,
        meme_reaction: 'none',
        sfx_reaction: 'pop.wav'
      },
      {
        id: 'c5',
        username: '_minkdesu',
        handle: '_minkdesu',
        avatar_url: 'https://api.dicebear.com/7.x/avataaars/svg?seed=minkdesu',
        content: 'Các đồng chí công an đang gõ phím trả lời nhiệt tình lắm đó nha bro =)))',
        likes: '6,8K',
        likes_count: 6800,
        replies: 81,
        reposts: '210',
        shares: '145',
        time_ago: '',
        selected: true,
        meme_reaction: 'none',
        sfx_reaction: 'cartoon_laugh.wav'
      }
    ];

    document.getElementById('postCardContainer').classList.remove('hidden');
    const appendBtn = document.getElementById('btnAppendChatImages');
    if (appendBtn) appendBtn.classList.add('hidden');
    document.getElementById('postAuthor').innerText = currentPost.username;
    document.getElementById('postAvatar').src = currentPost.avatar_url;
    document.getElementById('postLikes').innerText = `❤️ ${currentPost.likes} likes`;
    document.getElementById('postContent').value = currentPost.content;
    document.getElementById('postPlatformTag').innerText = 'THREADS';
    renderComments();
    return;
  } else if (presetKey === 'threads_real') {
    setTemplate('threads');
    setDarkMode(false);
    document.getElementById('urlInput').value = 'https://www.threads.net/@_anthuyen07_/post/DeXRJDDE-f-';
    await scrapePost();
  } else if (presetKey === 'tiktok') {
    setTemplate('tiktok');
    setDarkMode(false);
    document.getElementById('urlInput').value = 'tiktok';
    await scrapePost();
  }
}

async function scrapePost() {
  const url = document.getElementById('urlInput').value.trim();
  const minLikes = parseInt(document.getElementById('minLikes').value) || 0;
  
  const spinner = document.getElementById('scrapeSpinner');
  const btn = document.getElementById('btnScrape');
  const btnLabel = document.getElementById('scrapeBtnLabel');
  
  spinner.classList.remove('hidden');
  btn.disabled = true;
  btnLabel.innerText = 'Đang cào dữ liệu...';

  try {
    const res = await fetch('/api/scrape', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: url || 'threads', min_likes: minLikes })
    });
    
    if (!res.ok) throw new Error('Không thể tải bài viết');
    const data = await res.json();
    
    currentPost = {
      id: data.post_id || 'post_01',
      username: data.author,
      handle: data.author_handle,
      avatar_url: data.avatar_url,
      image_url: data.image_url || null,
      content: data.content,
      likes: data.likes,
      replies: data.comments ? data.comments.length : 0,
      reposts: data.reposts || '0',
      shares: data.shares || '0',
      time_ago: '',
      is_op: true
    };
    
    currentComments = (data.comments || []).map(c => ({
      ...c,
      selected: true,
      meme_reaction: c.meme_reaction || 'none',
      sfx_reaction: c.sfx_reaction || 'pop.wav'
    }));

    // Auto set BGM if recommended
    if (data.recommended_bgm) {
      const bgmSelect = document.getElementById('bgmSelect');
      if (bgmSelect) bgmSelect.value = data.recommended_bgm;
      const badge = document.getElementById('bgmBadgeText');
      if (badge) badge.innerText = '🤖 ' + (data.bgm_reason || 'Tự động chọn nhạc');
    }

    // Auto switch template to match scraped platform
    if (data.platform) {
      setTemplate(data.platform);
    }

    // Update Post Card in UI
    document.getElementById('postCardContainer').classList.remove('hidden');
    const appendBtn = document.getElementById('btnAppendChatImages');
    if (appendBtn) appendBtn.classList.add('hidden');
    document.getElementById('postAuthor').innerText = currentPost.username;
    document.getElementById('postAvatar').src = currentPost.avatar_url || 'https://api.dicebear.com/7.x/avataaars/svg?seed=' + currentPost.username;
    document.getElementById('postLikes').innerText = `❤️ ${currentPost.likes} likes`;
    document.getElementById('postContent').value = currentPost.content;
    document.getElementById('postPlatformTag').innerText = data.platform.toUpperCase();

    renderComments();
  } catch (err) {
    alert('Lỗi tải dữ liệu: ' + err.message);
  } finally {
    spinner.classList.add('hidden');
    btn.disabled = false;
    btnLabel.innerText = 'Cào Dữ Liệu';
  }
}

function renderComments() {
  const container = document.getElementById('commentsList');
  container.innerHTML = '';
  
  let selectedCount = 0;
  
  if (currentComments.length === 0) {
    container.innerHTML = `
      <div class="text-center py-8 text-gray-500 text-xs">
        Chưa có bình luận nào. Bạn có thể bấm <b>"Thêm 1 bình luận"</b> hoặc <b>"Dán nhanh nhiều bình luận"</b> để tạo ngay!
      </div>
    `;
    document.getElementById('selectedCountBadge').innerText = `Đã chọn: 0`;
    return;
  }

  currentComments.forEach((c, idx) => {
    if (c.selected) selectedCount++;
    
    const memeVal = c.meme_reaction || 'none';
    const sfxVal = c.sfx_reaction || 'pop.wav';

    const itemDiv = document.createElement('div');
    itemDiv.className = `p-3 rounded-xl border transition ${c.selected ? 'bg-gray-900/90 border-indigo-500/50' : 'bg-gray-950/40 border-gray-800 opacity-60'}`;
    
    itemDiv.innerHTML = `
      <div class="flex items-start justify-between space-x-3 mb-2">
        <div class="flex items-center space-x-2.5">
          <input type="checkbox" ${c.selected ? 'checked' : ''} 
                 onchange="toggleCommentSelection(${idx}, this.checked)"
                 class="w-4 h-4 rounded border-gray-700 text-indigo-600 focus:ring-0 cursor-pointer">
          <img src="${c.avatar_url || 'https://api.dicebear.com/7.x/avataaars/svg?seed=' + c.username}" class="w-6 h-6 rounded-full border border-gray-700">
          <input type="text" value="${c.username}" onchange="updateCommentAuthor(${idx}, this.value)"
                 class="bg-transparent font-bold text-xs text-gray-200 border-b border-transparent hover:border-gray-600 focus:outline-none focus:border-indigo-500 px-0.5">
        </div>
        <div class="flex items-center space-x-2">
          <span class="text-xs font-semibold text-rose-400">❤️ ${c.likes}</span>
          <button onclick="deleteComment(${idx})" title="Xóa bình luận này" class="text-gray-500 hover:text-red-400 p-1 text-xs">
            🗑
          </button>
        </div>
      </div>

      <textarea rows="2" onchange="updateCommentContent(${idx}, this.value)"
                class="w-full bg-gray-950/70 border border-gray-800 rounded-lg p-2 text-xs text-gray-300 focus:outline-none focus:border-indigo-500 transition">${c.content}</textarea>
      ${c.card_url ? `<div class="mt-2"><img src="${c.card_url}" class="max-h-24 rounded-lg border border-amber-500/40 shadow object-contain"></div>` : ''}

      <!-- Meme Reaction & SFX Controls Row -->
      <div class="mt-2.5 pt-2 border-t border-gray-800/80 flex items-center justify-between text-[11px] gap-2 flex-wrap">
        <div class="flex items-center space-x-1.5">
          <span class="text-indigo-300 font-semibold">🎭 Sticker 3D:</span>
          <select onchange="updateCommentMeme(${idx}, this.value)" class="bg-gray-950 border border-gray-700 rounded px-1.5 py-0.5 text-[11px] text-white focus:outline-none focus:border-indigo-500">
            <option value="none" ${memeVal === 'none' ? 'selected' : ''}>Không hiện (Chuẩn TikTok)</option>
            <option value="meme_laugh.png" ${memeVal === 'meme_laugh.png' ? 'selected' : ''}>🤣 Cười 3D Fluent</option>
            <option value="meme_cry.png" ${memeVal === 'meme_cry.png' ? 'selected' : ''}>😭 Khóc 3D Fluent</option>
            <option value="meme_shock.png" ${memeVal === 'meme_shock.png' ? 'selected' : ''}>😱 Sốc 3D Fluent</option>
            <option value="meme_clown.png" ${memeVal === 'meme_clown.png' ? 'selected' : ''}>🤡 Chú hề 3D Fluent</option>
            <option value="meme_dead.png" ${memeVal === 'meme_dead.png' ? 'selected' : ''}>💀 Bất lực 3D Fluent</option>
            <option value="meme_fire.png" ${memeVal === 'meme_fire.png' ? 'selected' : ''}>🔥 Quá cháy 3D Fluent</option>
            <option value="laughing_cat.png" ${memeVal === 'laughing_cat.png' ? 'selected' : ''}>🐱 Mèo cười</option>
          </select>
          ${memeVal !== 'none' ? `<img src="/assets/memes/${memeVal}" class="w-6 h-6 rounded border border-indigo-500/50 object-contain shadow">` : ''}
        </div>

        <div class="flex items-center space-x-1.5">
          <span class="text-gray-400">🔊 SFX:</span>
          <select onchange="updateCommentSFX(${idx}, this.value)" class="bg-gray-950 border border-gray-700 rounded px-1.5 py-0.5 text-[11px] text-white focus:outline-none focus:border-indigo-500">
            <option value="pop.wav" ${sfxVal === 'pop.wav' ? 'selected' : ''}>Tiếng Pop (Nhẹ nhàng)</option>
            <option value="bruh.wav" ${sfxVal === 'bruh.wav' ? 'selected' : ''}>Tiếng Bruh (Hài hước)</option>
            <option value="vine_boom.wav" ${sfxVal === 'vine_boom.wav' ? 'selected' : ''}>Tiếng Vine Boom (Sốc)</option>
            <option value="cartoon_laugh.wav" ${sfxVal === 'cartoon_laugh.wav' ? 'selected' : ''}>Tiếng cười hả hê</option>
          </select>
          <button onclick="previewItemTTS('c_${idx}')" class="px-2 py-0.5 rounded bg-gray-800 hover:bg-gray-700 text-indigo-300 ml-1">
            🔊 Nghe thử
          </button>
        </div>
      </div>
    `;
    container.appendChild(itemDiv);
  });

  document.getElementById('selectedCountBadge').innerText = `Đã chọn: ${selectedCount}`;
}

function toggleCommentSelection(index, checked) {
  currentComments[index].selected = checked;
  renderComments();
}

function updateCommentAuthor(index, newAuthor) {
  currentComments[index].username = newAuthor;
}

function updateCommentContent(index, newText) {
  currentComments[index].content = newText;
}

function updateCommentMeme(index, memeFile) {
  currentComments[index].meme_reaction = memeFile;
  // Auto-set matching SFX
  if (memeFile === 'meme_cry.png' || memeFile === 'meme_clown.png') {
    currentComments[index].sfx_reaction = 'bruh.wav';
  } else if (memeFile === 'meme_shock.png' || memeFile === 'meme_dead.png') {
    currentComments[index].sfx_reaction = 'vine_boom.wav';
  } else if (memeFile === 'meme_laugh.png' || memeFile === 'laughing_cat.png' || memeFile === 'meme_fire.png') {
    currentComments[index].sfx_reaction = 'cartoon_laugh.wav';
  } else if (memeFile === 'none') {
    currentComments[index].sfx_reaction = 'pop.wav';
  }
  renderComments();
}

function updateCommentSFX(index, sfxFile) {
  currentComments[index].sfx_reaction = sfxFile;
}

function deleteComment(index) {
  currentComments.splice(index, 1);
  renderComments();
}

function addNewComment() {
  const newIdx = currentComments.length + 1;
  currentComments.push({
    id: `c_custom_${Date.now()}`,
    username: `Người xem ${newIdx}`,
    handle: `nguoixem_${newIdx}`,
    avatar_url: `https://api.dicebear.com/7.x/avataaars/svg?seed=user_${newIdx}`,
    content: "Bình luận mới...",
    likes: "150",
    likes_count: 150,
    replies: 0,
    time_ago: "vừa xong",
    selected: true,
    meme_reaction: 'meme_laugh.png',
    sfx_reaction: 'cartoon_laugh.wav'
  });
  renderComments();
}

// Bulk Paste Modal Logic
function openBulkModal() {
  document.getElementById('bulkModal').classList.remove('hidden');
}

function closeBulkModal() {
  document.getElementById('bulkModal').classList.add('hidden');
}

function applyBulkComments() {
  const rawText = document.getElementById('bulkTextInput').value.trim();
  if (!rawText) {
    closeBulkModal();
    return;
  }
  
  const lines = rawText.split('\n').map(l => l.trim()).filter(l => l.length > 0);
  lines.forEach((line, idx) => {
    currentComments.push({
      id: `c_bulk_${Date.now()}_${idx}`,
      username: `Khán giả ${currentComments.length + 1}`,
      handle: `khangia_${currentComments.length + 1}`,
      avatar_url: `https://api.dicebear.com/7.x/avataaars/svg?seed=bulk_${idx}`,
      content: line,
      likes: `${Math.floor(Math.random() * 800) + 50}`,
      likes_count: 100,
      replies: 0,
      time_ago: "vừa xong",
      selected: true,
      meme_reaction: 'none',
      sfx_reaction: 'pop.wav'
    });
  });

  document.getElementById('bulkTextInput').value = '';
  closeBulkModal();
  renderComments();
}

let isPreviewingTTS = false;
async function previewItemTTS(type) {
  if (isPreviewingTTS) return;
  isPreviewingTTS = true;

  let text = '';
  if (type === 'post') {
    text = document.getElementById('postContent').value;
  } else {
    const idx = parseInt(type.replace('c_', ''));
    text = currentComments[idx].content;
  }
  
  const voice = document.getElementById('voiceSelect').value;
  const rateVal = document.getElementById('speechRate').value;
  const rate = rateVal >= 0 ? `+${rateVal}%` : `${rateVal}%`;

  try {
    const res = await fetch(`/api/preview-tts?text=${encodeURIComponent(text)}&voice=${voice}&rate=${rate}`, {
      method: 'POST'
    });
    const data = await res.json();
    const audio = new Audio(data.audio_url);
    audio.play();
  } catch (e) {
    alert('Không thể phát âm thanh: ' + e.message);
  } finally {
    isPreviewingTTS = false;
  }
}

async function uploadCustomBackground(input) {
  if (!input.files || input.files.length === 0) return;
  const file = input.files[0];
  const formData = new FormData();
  formData.append('file', file);
  
  try {
    const res = await fetch('/api/upload-background', {
      method: 'POST',
      body: formData
    });
    if (!res.ok) throw new Error('Không thể tải file lên');
    const data = await res.json();
    
    const select = document.getElementById('bgSelect');
    const opt = document.createElement('option');
    opt.value = data.filename;
    opt.innerText = `🎥 ${data.filename} (Tải lên)`;
    opt.selected = true;
    select.prepend(opt);
    alert('Đã tải lên và chọn video nền mới thành công: ' + data.filename);
  } catch (e) {
    alert('Lỗi tải video nền: ' + e.message);
  }
}

async function uploadChatScreenshots(input, appendMode = false) {
  if (!input.files || input.files.length === 0) return;
  const formData = new FormData();
  Array.from(input.files).forEach(f => formData.append('files', f));

  const btnLabel = document.getElementById('scrapeBtnLabel');
  if (btnLabel) btnLabel.innerText = 'Đang tách ảnh chat...';

  try {
    const res = await fetch('/api/upload-chat-images', {
      method: 'POST',
      body: formData
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Không thể tách dòng tin nhắn từ ảnh');

    currentPost = null;
    document.getElementById('postCardContainer').classList.add('hidden');
    const appendBtn = document.getElementById('btnAppendChatImages');
    if (appendBtn) appendBtn.classList.remove('hidden');

    if (appendMode && currentComments.length > 0 && currentComments[0].is_chat_strip) {
      currentComments = currentComments.concat(data.items);
    } else {
      currentComments = data.items;
      const vSel = document.getElementById('voiceSelect');
      if (vSel && vSel.value === 'vi-VN-HoaiMyNeural') {
        vSel.value = 'dialogue-auto';
      }
    }
    renderComments();
  } catch (e) {
    alert('Lỗi đọc ảnh tin nhắn: ' + e.message);
  } finally {
    input.value = '';
    if (btnLabel) btnLabel.innerText = 'Cào Dữ Liệu';
  }
}

async function renderVideo() {
  const selectedList = currentComments.filter(c => c.selected);
  if (!currentPost && selectedList.length === 0) {
    alert('Vui lòng tải ảnh tin nhắn hoặc cào bài viết trước khi render!');
    return;
  }

  // Sync edited post text when in social mode
  if (currentPost) {
    currentPost.content = document.getElementById('postContent').value;
  }

  if (selectedList.length === 0) {
    alert('Vui lòng chọn ít nhất 1 dòng tin nhắn / bình luận để làm video!');
    return;
  }

  const voice = document.getElementById('voiceSelect').value;
  const rateVal = document.getElementById('speechRate').value;
  const rate = rateVal >= 0 ? `+${rateVal}%` : `${rateVal}%`;
  const bgmVol = parseFloat(document.getElementById('bgmVolume').value) / 100.0;
  const sfxVol = parseFloat(document.getElementById('sfxVolume').value) / 100.0;

  const bgFile = document.getElementById('bgSelect') ? document.getElementById('bgSelect').value : 'minecraft_parkour.mp4';
  const bgmFile = document.getElementById('bgmSelect') ? document.getElementById('bgmSelect').value : 'meme_tiktok_viral.wav';
  const runningTxt = document.getElementById('runningTextInput') ? document.getElementById('runningTextInput').value : '@Vietcaigido?';
  const runningMode = document.getElementById('runningTextMode') ? document.getElementById('runningTextMode').value : 'top_left';
  const hasBadge = document.getElementById('chkBadge') ? document.getElementById('chkBadge').checked : false;

  const payload = {
    platform: currentPlatform,
    dark_mode: isDarkMode,
    voice: voice,
    speech_rate: rate,
    post: currentPost,
    comments: currentComments,
    background_file: bgFile,
    bgm_file: bgmFile,
    bgm_volume: bgmVol,
    sfx_volume: sfxVol,
    running_text: runningTxt,
    running_text_mode: runningMode,
    channel_badge: hasBadge
  };

  const btn = document.getElementById('btnRender');
  const btnText = document.getElementById('renderBtnText');
  const progressBox = document.getElementById('renderProgressBox');
  const progressBar = document.getElementById('progressBar');
  const pctBadge = document.getElementById('progressPercentBadge');
  const statusTxt = document.getElementById('progressStatusText');
  const elapsedTxt = document.getElementById('progressElapsedText');
  const resultWrapper = document.getElementById('videoResultWrapper');

  btn.disabled = true;
  btn.classList.add('opacity-50');
  progressBox.classList.remove('hidden');
  resultWrapper.classList.add('hidden');
  btnText.innerText = 'ĐANG TẠO VIDEO...';
  if (progressBar) progressBar.style.width = '2%';
  if (pctBadge) pctBadge.innerText = '2%';
  if (statusTxt) statusTxt.innerText = 'Đang khởi tạo luồng xử lý...';
  if (elapsedTxt) elapsedTxt.innerText = '⏱ 0.0s';

  try {
    const startRes = await fetch('/api/render-jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    if (!startRes.ok) {
      const err = await startRes.json();
      throw new Error(err.detail || 'Lỗi khởi tạo tiến trình render');
    }

    const { job_id } = await startRes.json();
    let finalData = null;

    while (true) {
      await new Promise(r => setTimeout(r, 400));
      const pollRes = await fetch(`/api/render-jobs/${job_id}`);
      if (!pollRes.ok) throw new Error('Mất kết nối tới tiến trình render');
      const job = await pollRes.json();

      const pct = Math.max(2, Math.min(100, job.percent || 0));
      if (progressBar) progressBar.style.width = `${pct}%`;
      if (pctBadge) pctBadge.innerText = `${pct}%`;
      if (statusTxt) statusTxt.innerText = job.stage || 'Đang xử lý...';
      if (elapsedTxt) elapsedTxt.innerText = `⏱ ${(job.elapsed_sec || 0).toFixed(1)}s`;
      btnText.innerText = `ĐANG RENDER (${pct}%)...`;

      if (job.status === 'completed' && job.result) {
        finalData = job.result;
        break;
      }
      if (job.status === 'failed') {
        throw new Error(job.error || 'Lỗi xử lý render video');
      }
    }

    const player = document.getElementById('finalVideoPlayer');
    player.src = finalData.video_url;
    player.load();

    const downloadBtn = document.getElementById('btnDownloadVideo');
    downloadBtn.href = finalData.video_url;
    downloadBtn.download = finalData.filename;

    resultWrapper.classList.remove('hidden');
    player.scrollIntoView({ behavior: 'smooth' });
  } catch (err) {
    alert('Lỗi Render Video: ' + err.message);
    progressBox.classList.add('hidden');
  } finally {
    btn.disabled = false;
    btn.classList.remove('opacity-50');
    btnText.innerText = 'TẠO VIDEO SHORTS NGAY';
  }
}

// ------------------------------------------------------------------
// Telegram Bot & Admin Key Management (@citywin2026)
// ------------------------------------------------------------------
async function openTelegramModal() {
  document.getElementById('telegramModal').classList.remove('hidden');
  await refreshBotStatus();
}

function closeTelegramModal() {
  document.getElementById('telegramModal').classList.add('hidden');
}

async function refreshBotStatus(autoOpenIfNoToken = false) {
  try {
    const res = await fetch('/api/bot/status');
    if (!res.ok) return;
    const data = await res.json();

    const badge = document.getElementById('botStatusBadge');
    const navDot = document.getElementById('navBotDot');
    const tokenInfo = document.getElementById('botCurrentTokenInfo');

    if (data.is_running) {
      badge.className = "px-2.5 py-0.5 text-[11px] rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 font-bold";
      badge.innerText = `🟢 Đang chạy: @${data.bot_username}`;
      if (navDot) navDot.className = "w-2 h-2 rounded-full bg-emerald-400 animate-ping mr-2";
    } else {
      badge.className = "px-2.5 py-0.5 text-[11px] rounded-full bg-amber-500/20 text-amber-300 border border-amber-500/30";
      badge.innerText = "⚪ Chưa nhập Token / Đang dừng";
      if (navDot) navDot.className = "w-2 h-2 rounded-full bg-amber-400 mr-2";
      if (autoOpenIfNoToken && !data.has_token) {
        document.getElementById('telegramModal').classList.remove('hidden');
      }
    }

    if (tokenInfo) {
      tokenInfo.innerText = data.masked_token ? `Token đã lưu: ${data.masked_token}` : "Chưa lưu Token";
    }

    renderKeysTable(data.keys || []);
  } catch (e) {
    console.error("Failed to fetch bot status:", e);
  }
}

function renderKeysTable(keys) {
  const container = document.getElementById('keysTableContainer');
  if (!container) return;
  container.innerHTML = '';

  if (keys.length === 0) {
    container.innerHTML = `<div class="text-center py-6 text-gray-500 text-xs">Chưa có Key nào. Hãy bấm <b>"➕ Tạo 1 Key"</b> để tạo mã kích hoạt!</div>`;
    return;
  }

  keys.forEach(k => {
    const isBound = k.bound_user_id !== null && k.bound_user_id !== undefined;
    const userDisplay = isBound
      ? `<span class="text-sky-300 font-semibold">👤 @${k.bound_username || k.bound_full_name || 'User'} (ID: ${k.bound_user_id})</span> • <span class="text-emerald-400">${k.videos_created || 0} video</span>`
      : `<span class="text-emerald-400 font-medium">🟢 Chưa kích hoạt (Sẵn sàng)</span>`;

    const row = document.createElement('div');
    row.className = "flex items-center justify-between p-2.5 rounded-xl bg-gray-950 border border-gray-800 text-xs gap-2 flex-wrap";
    row.innerHTML = `
      <div class="space-y-0.5">
        <div class="flex items-center space-x-2">
          <code class="px-2 py-0.5 rounded bg-indigo-500/20 text-indigo-300 font-bold border border-indigo-500/30 select-all">${k.key_code}</code>
          <button onclick="copyKeyCmd('${k.key_code}')" class="text-[11px] px-2 py-0.5 rounded bg-gray-800 hover:bg-gray-700 text-gray-300 transition">📋 Copy lệnh /key</button>
          ${k.note ? `<span class="text-[11px] text-gray-400 italic">[${k.note}]</span>` : ''}
        </div>
        <div class="text-[11px] text-gray-400 pl-1">
          ${userDisplay}
        </div>
      </div>
      <div>
        <button onclick="deleteBotKey('${k.key_code}')" class="px-2.5 py-1 rounded-lg bg-rose-900/50 hover:bg-rose-700 text-rose-200 border border-rose-700/50 text-[11px] font-semibold transition">
          🗑 Xóa & Vô hiệu hóa
        </button>
      </div>
    `;
    container.appendChild(row);
  });
}

function copyKeyCmd(keyCode) {
  navigator.clipboard.writeText(`/key ${keyCode}`);
  alert(`Đã copy lệnh kích hoạt: /key ${keyCode}`);
}

async function startTelegramBot() {
  const token = document.getElementById('botTokenInput').value.trim();
  const btn = document.getElementById('btnStartBot');
  if (!token) {
    alert('Vui lòng dán mã Telegram Bot Token (từ @BotFather) vào ô trống!');
    return;
  }
  btn.disabled = true;
  btn.innerText = '⏳ Đang kết nối...';
  try {
    const res = await fetch('/api/bot/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Không thể khởi động Bot');
    document.getElementById('botTokenInput').value = '';
    await refreshBotStatus();
    alert(data.message);
  } catch (e) {
    alert('Lỗi: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.innerText = '🚀 Khởi Chạy Bot';
  }
}

async function stopTelegramBot() {
  try {
    await fetch('/api/bot/stop', { method: 'POST' });
    await refreshBotStatus();
  } catch (e) {
    alert('Lỗi khi dừng Bot: ' + e.message);
  }
}

async function createBotKeys(count) {
  const note = document.getElementById('keyNoteInput').value.trim();
  try {
    const res = await fetch('/api/bot/keys', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ note, count })
    });
    const data = await res.json();
    document.getElementById('keyNoteInput').value = '';
    renderKeysTable(data.keys || []);
  } catch (e) {
    alert('Lỗi tạo Key: ' + e.message);
  }
}

async function deleteBotKey(keyCode) {
  try {
    const res = await fetch(`/api/bot/keys/${encodeURIComponent(keyCode)}`, {
      method: 'DELETE'
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Không thể xóa Key');
    renderKeysTable(data.keys || []);
  } catch (e) {
    alert('Lỗi xóa Key: ' + e.message);
  }
}
