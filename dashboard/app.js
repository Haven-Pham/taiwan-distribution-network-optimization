/* Renders the network explorer from data.json. Colours follow each DC, so a DC keeps its colour as others are added. */
(() => {
  'use strict';

  const I18N = {
    en: {
      skip: 'Skip to content', eyebrow: 'Network design · OR-Tools', title: 'Where should distribution centres go in Taiwan?',
      lede: 'Real population and road data, with a fictional retailer sending 20 million parcels a year. Pick a number of DCs to see the network OR-Tools chose, then what it costs and how it serves people.',
      countLabel: 'Number of DCs', showRoutes: 'Show one day of truck routes',
      costTitle: 'Annual cost for each number of DCs (NT$ million)', dcTitle: 'Distribution centres in this network',
      howTitle: 'How to read this',
      how1: 'Each network is the proven lowest-cost choice from 32 candidate sites for that number of DCs, under the assumed costs. Costs then come from routing trucks on simulated days.',
      how2: 'Every township station must be reached by 09:00 on a truck leaving at 03:00, and the truck must return within 10 hours. That promise is why every network here has a DC east of the mountains.',
      how3: 'The company, its volume and all costs are assumptions, so compare the options and do not read the amounts as a budget. Differences below about 1% are too small to separate.',
      how4: 'Each township is one station, with parcels in proportion to population. Times are car times from OpenStreetMap data multiplied by 1.25, without traffic or road closures. Offshore islands are excluded.',
      by: 'Pham Hong Hieu (Haven)', code: 'Code and full analysis', portfolio: 'Portfolio',
      credit: 'Data: Ministry of the Interior via data.gov.tw (Open Government Data License 1.0). Road data © OpenStreetMap contributors (ODbL), through OSRM.',
      theme: ['Dark mode', 'Light mode'], kTotal: 'Total cost a year', kParcel: 'Cost a parcel', k2h: 'Population within 2 truck hours', kLongest: 'Longest trip', kTrucks: 'Trucks a day',
      million: v => `NT$${fmt(v / 1e6, 0)}M`, hours: v => `${fmt(v, 1)} h`, fixed: 'DC fixed cost', delivery: 'Truck routes', handling: 'Handling', linehaul: 'Linehaul',
      dc: 'DC', towns: 'Townships', share: 'Population %', load: 'Capacity used %', far: 'Farthest (h)',
      mapTitle: k => `Which DC serves each township: ${k} DCs`,
      mapNote: n => `Move over or tab to a township for details. Stars mark DCs. Colours follow the table.${n ? ' Route lines join stops directly and do not follow roads.' : ''}`,
      routesOff: 'Routes are available for the recommended 4-DC network',
      tip: (t, dc, h) => `<b>${t}</b><br>Served from ${dc}<br>${fmt(h, 1)} truck hours`,
      takeaway: (k, n, best, three) => k === best
        ? `Recommended. This costs the same as 3 DCs (within ${fmt(Math.abs(n.total / three.total - 1) * 100, 1)}%), but brings ${fmt(n.within2h, 1)}% of people within 2 truck hours instead of ${fmt(three.within2h, 1)}%.`
        : k < best ? `Nearly the same cost as 4 DCs, but only ${fmt(n.within2h, 1)}% of people are within 2 truck hours.`
          : `${fmt((n.total / DATA.networks[best].total - 1) * 100, 0)}% more cost than the recommended 4 DCs. Fixed cost outweighs what the extra sites save on trucks.`,
      mapLabel: k => `Map of Taiwan with townships coloured by which of ${k} distribution centres serves them. The table beside the map gives the same information.`,
    },
    zh: {
      skip: '跳至主要內容', eyebrow: '物流網路設計 · OR-Tools', title: '臺灣的物流中心該設在哪裡？',
      lede: '採用真實人口與道路資料，並假設一家每年配送 2,000 萬件包裹的網路零售商。選擇物流中心數量，即可查看 OR-Tools 選出的網路、成本與服務範圍。',
      countLabel: '物流中心數量', showRoutes: '顯示一日貨車路線',
      costTitle: '各物流中心數量的年度成本（百萬元）', dcTitle: '此網路的物流中心',
      howTitle: '如何解讀',
      how1: '每個網路都是在假設成本下，從 32 個候選地點中經證明成本最低的選擇。成本則由模擬日的貨車路線計算。',
      how2: '貨車 03:00 出發，須於 09:00 前抵達各鄉鎮配送站，並於 10 小時內返回。正因如此，此處每個網路都在山脈以東設有物流中心。',
      how3: '公司、貨量與所有成本皆為假設，請比較各方案，不應將金額視為預算。約 1% 以內的差異無法區分。',
      how4: '每個鄉鎮市區為一個配送站，包裹量與人口成比例。時間為 OpenStreetMap 資料的小客車時間乘以 1.25，未計入交通與道路封閉。離島不納入。',
      by: '范鴻孝 Pham Hong Hieu (Haven)', code: '程式碼與完整分析', portfolio: '作品集',
      credit: '資料來源：內政部，經政府資料開放平臺取得（政府資料開放授權條款第 1 版）。道路資料 © OpenStreetMap 貢獻者（ODbL），經由 OSRM。',
      theme: ['深色模式', '淺色模式'], kTotal: '年度總成本', kParcel: '每件成本', k2h: '2 小時車程內人口', kLongest: '最長車程', kTrucks: '每日貨車數',
      million: v => `${fmt(v / 1e8, 2)} 億元`, hours: v => `${fmt(v, 1)} 小時`, fixed: '物流中心固定成本', delivery: '貨車路線', handling: '處理費', linehaul: '幹線運輸',
      dc: '物流中心', towns: '鄉鎮市區數', share: '人口 %', load: '產能使用 %', far: '最遠（小時）',
      mapTitle: k => `各鄉鎮市區由哪個物流中心服務：${k} 個物流中心`,
      mapNote: n => `將游標移至或以 Tab 鍵選擇鄉鎮市區可查看詳情。星號為物流中心，顏色與表格相同。${n ? '路線直接連接各站，並非實際道路。' : ''}`,
      routesOff: '路線僅提供建議的 4 個物流中心網路',
      tip: (t, dc, h) => `<b>${t}</b><br>由${dc}服務<br>貨車 ${fmt(h, 1)} 小時`,
      takeaway: (k, n, best, three) => k === best
        ? `建議方案。成本與 3 個物流中心相當（差距 ${fmt(Math.abs(n.total / three.total - 1) * 100, 1)}% 以內），但 2 小時車程內人口達 ${fmt(n.within2h, 1)}%，而非 ${fmt(three.within2h, 1)}%。`
        : k < best ? `成本與 4 個物流中心相當，但 2 小時車程內人口僅 ${fmt(n.within2h, 1)}%。`
          : `成本較建議的 4 個物流中心高 ${fmt((n.total / DATA.networks[best].total - 1) * 100, 0)}%，固定成本超過新增據點節省的運輸費用。`,
      mapLabel: k => `臺灣地圖，依 ${k} 個物流中心的服務範圍為鄉鎮市區著色。地圖旁的表格提供相同資訊。`,
    },
  };

  /* A fixed, validated order for each theme: a DC's colour never depends on its rank. */
  const SERIES = {
    light: ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948'],
    dark: ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#008300', '#9085e9', '#e66767'],
  };
  const PART_SLOTS = [['fixed', 0], ['delivery', 1], ['handling', null], ['linehaul', 3]];
  const CAPACITY = 12000000;
  const W = 560, H = 760, LON = [119.98, 122.05], LAT = [21.85, 25.35];
  const COS = Math.cos(23.7 * Math.PI / 180);

  let DATA, lang = 'en', count = 4, routesOn = false;
  const $ = id => document.getElementById(id);
  const t = key => I18N[lang][key];
  const dark = () => document.documentElement.dataset.theme === 'dark';
  const fmt = (v, d = 1) => Number(v).toLocaleString(lang === 'zh' ? 'zh-TW' : 'en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
  const colour = site => SERIES[dark() ? 'dark' : 'light'][DATA.colourOrder.indexOf(site)];
  const name = index => DATA.towns[index][lang];

  const scale = Math.min(W / ((LON[1] - LON[0]) * COS), H / (LAT[1] - LAT[0]));
  const px = lon => (lon - LON[0]) * COS * scale;
  const py = lat => (LAT[1] - lat) * scale;
  const path = rings => rings.map(ring => `M${ring.map(([x, y]) => `${px(x).toFixed(1)},${py(y).toFixed(1)}`).join('L')}Z`).join('');

  function renderMap() {
    const network = DATA.networks[count], svg = $('map');
    svg.setAttribute('viewBox', `0 0 ${Math.ceil(px(LON[1]))} ${H}`);
    svg.setAttribute('aria-label', t('mapLabel')(count));
    const towns = DATA.towns.map((_, i) => `<path class="town" tabindex="0" data-i="${i}" d="${path(DATA.outlines[i])}" fill="${colour(network.assignment[i])}" fill-opacity=".78"></path>`).join('');
    const routes = routesOn && count === DATA.best
      ? DATA.routes.map(route => `<polyline class="route" points="${route.map(i => `${px(DATA.towns[i].lon).toFixed(1)},${py(DATA.towns[i].lat).toFixed(1)}`).join(' ')}"></polyline>`).join('') : '';
    const marks = network.sites.map(site => {
      const x = px(DATA.towns[site].lon), y = py(DATA.towns[site].lat);
      const star = Array.from({ length: 10 }, (_, k) => {
        const r = k % 2 ? 4 : 10, a = -Math.PI / 2 + k * Math.PI / 5;
        return `${(x + r * Math.cos(a)).toFixed(1)},${(y + r * Math.sin(a)).toFixed(1)}`;
      }).join(' ');
      const east = DATA.towns[site].lon > 121.2;
      return `<polygon class="dc-mark" points="${star}"></polygon><text class="dc-label" x="${(x + (east ? -13 : 13)).toFixed(1)}" y="${(y + 4).toFixed(1)}" text-anchor="${east ? 'end' : 'start'}">${name(site)}</text>`;
    }).join('');
    svg.innerHTML = towns + routes + marks;
    $('map-title').textContent = t('mapTitle')(count);
    $('map-note').textContent = t('mapNote')(routesOn && count === DATA.best);
  }

  function showTip(target, x, y) {
    const i = Number(target.dataset.i), network = DATA.networks[count], tip = $('tip');
    tip.innerHTML = t('tip')(name(i), name(network.assignment[i]), network.hours[i]);
    tip.hidden = false;
    const box = $('map').parentElement.getBoundingClientRect();
    tip.style.left = `${Math.min(x - box.left + 12, box.width - tip.offsetWidth - 4)}px`;
    tip.style.top = `${Math.max(y - box.top - tip.offsetHeight - 8, 0)}px`;
  }

  function renderKpis() {
    const n = DATA.networks[count];
    const cards = [[t('kTotal'), t('million')(n.total)], [t('kParcel'), `NT$${fmt(n.total / DATA.parcels, 1)}`], [t('k2h'), `${fmt(n.within2h, 1)}%`], [t('kLongest'), t('hours')(n.longest)], [t('kTrucks'), fmt(n.trucks, 0)]];
    $('kpis').innerHTML = cards.map(([l, v]) => `<div class="kpi"><span class="label">${l}</span><span class="value">${v}</span></div>`).join('');
  }

  function renderCosts() {
    const counts = Object.keys(DATA.networks).map(Number), top = Math.max(...counts.map(k => DATA.networks[k].total));
    const series = SERIES[dark() ? 'dark' : 'light'];
    const fill = slot => (slot === null ? 'var(--muted)' : series[slot]);
    $('cost-bars').innerHTML = counts.map(k => {
      const n = DATA.networks[k];
      const parts = PART_SLOTS.map(([key, slot]) => `<i style="width:${(n[key] / top * 100).toFixed(2)}%;background:${fill(slot)}" title="${t(key)}: ${fmt(n[key] / 1e6, 0)}"></i>`).join('');
      return `<div class="cost-row${k === count ? ' active' : ''}"><span>${k}</span><div class="bar">${parts}</div><span style="text-align:right">${fmt(n.total / 1e6, 0)}</span></div>`;
    }).join('');
    $('cost-legend').innerHTML = PART_SLOTS.map(([key, slot]) => `<span><i style="background:${fill(slot)}"></i>${t(key)}</span>`).join('');
  }

  function renderTable() {
    const n = DATA.networks[count], total = DATA.towns.reduce((sum, town) => sum + town.pop, 0);
    const rows = n.sites.map(site => {
      const served = n.assignment.map((a, i) => (a === site ? i : -1)).filter(i => i >= 0);
      const pop = served.reduce((sum, i) => sum + DATA.towns[i].pop, 0);
      return { site, towns: served.length, share: pop / total * 100, load: pop / total * DATA.parcels / CAPACITY * 100, far: Math.max(...served.map(i => n.hours[i])) };
    }).sort((a, b) => b.share - a.share);
    $('dc-table').innerHTML = `<thead><tr>${[t('dc'), t('towns'), t('share'), t('load'), t('far')].map(h => `<th scope="col">${h}</th>`).join('')}</tr></thead><tbody>${
      rows.map(r => `<tr><td><span class="swatch" style="background:${colour(r.site)}"></span>${name(r.site)}</td><td>${r.towns}</td><td>${fmt(r.share)}</td><td>${fmt(r.load, 0)}</td><td>${fmt(r.far)}</td></tr>`).join('')}</tbody>`;
  }

  function renderControls() {
    $('counts').innerHTML = Object.keys(DATA.networks).map(k => `<button type="button" data-k="${k}" aria-pressed="${Number(k) === count}">${k}</button>`).join('');
    const box = $('show-routes'), available = count === DATA.best;
    box.disabled = !available;
    box.checked = routesOn && available;
    box.parentElement.setAttribute('aria-disabled', String(!available));
    box.parentElement.title = available ? '' : t('routesOff');
  }

  function renderText() {
    document.documentElement.lang = lang === 'zh' ? 'zh-Hant' : 'en';
    document.title = t('title');
    document.querySelectorAll('[data-i18n]').forEach(node => { node.textContent = t(node.dataset.i18n); });
    document.querySelectorAll('[data-lang]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.lang === lang)));
    $('theme').textContent = t('theme')[dark() ? 1 : 0];
    $('takeaway').textContent = t('takeaway')(count, DATA.networks[count], DATA.best, DATA.networks[3]);
  }

  function renderAll() {
    renderText(); renderControls(); renderKpis(); renderMap(); renderCosts(); renderTable();
  }

  function bind() {
    document.querySelectorAll('[data-lang]').forEach(b => b.addEventListener('click', () => {
      lang = b.dataset.lang;
      try { localStorage.setItem('net-lang', lang); } catch { /* the preference is optional */ }
      renderAll();
    }));
    $('theme').addEventListener('click', () => {
      const root = document.documentElement;
      root.dataset.theme = dark() ? 'light' : 'dark';
      try { localStorage.setItem('net-theme', root.dataset.theme); } catch { /* the preference is optional */ }
      renderAll();
    });
    $('counts').addEventListener('click', event => {
      const b = event.target.closest('button');
      if (b) { count = Number(b.dataset.k); renderAll(); }
    });
    $('show-routes').addEventListener('change', event => { routesOn = event.target.checked; renderMap(); });
    const map = $('map');
    map.addEventListener('mousemove', event => { if (event.target.dataset.i) showTip(event.target, event.clientX, event.clientY); else $('tip').hidden = true; });
    map.addEventListener('mouseleave', () => { $('tip').hidden = true; });
    map.addEventListener('focusin', event => {
      if (!event.target.dataset.i) return;
      const box = event.target.getBoundingClientRect();
      showTip(event.target, box.left + box.width / 2, box.top);
    });
    map.addEventListener('focusout', () => { $('tip').hidden = true; });
  }

  async function start() {
    const stored = key => { try { return localStorage.getItem(key); } catch { return null; } };
    const query = new URLSearchParams(location.search);
    const wanted = query.get('lang') || stored('net-lang');
    lang = wanted === 'zh' || wanted === 'en' ? wanted : (navigator.language.startsWith('zh') ? 'zh' : 'en');
    document.documentElement.dataset.theme = query.get('theme') || stored('net-theme') || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    const response = await fetch('data.json');
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    DATA = await response.json();
    count = DATA.best;
    bind();
    renderAll();
  }
  start().catch(error => {
    const message = document.createElement('p');
    message.className = 'takeaway';
    message.setAttribute('role', 'alert');
    message.textContent = `The data could not be loaded (${error.message}). Please reload the page.`;
    $('main').prepend(message);
  });
})();
