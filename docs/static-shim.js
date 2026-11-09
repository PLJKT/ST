/* ST v5 静态快照适配层
 * 把前端对 /api/* 的 GET 请求映射到预导出的 data/*.json。
 * 写操作（新增/修改/删除/模拟/预热）在静态托管下不可用，统一返回只读提示。
 */
(function () {
  "use strict";

  window.ST_STATIC = true;

  var origFetch = window.fetch.bind(window);
  var indexPromise = null;
  var searchPromise = null;
  var metaPromise = null;

  var V = (function () {
    var s = document.currentScript;
    var m = s && s.src ? s.src.match(/[?&]v=([^&]+)/) : null;
    return m ? m[1] : "";
  })();

  function withV(url) {
    if (!V) return url;
    return url + (url.indexOf("?") > -1 ? "&" : "?") + "v=" + V;
  }

  function loadJson(path) {
    return origFetch(withV(path), { cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error(path + " 载入失败");
      return r.json();
    });
  }

  function getIndex() {
    if (!indexPromise) indexPromise = loadJson("data/index.json");
    return indexPromise;
  }

  function getSearchIndex() {
    if (!searchPromise) searchPromise = loadJson("search-index.json");
    return searchPromise;
  }

  function jsonResponse(obj, status) {
    return Promise.resolve(new Response(JSON.stringify(obj), {
      status: status,
      headers: { "Content-Type": "application/json; charset=utf-8" }
    }));
  }

  function normQuery(search) {
    if (!search || search === "?") return "";
    var params = new URLSearchParams(search);
    var pairs = [];
    params.forEach(function (v, k) { pairs.push(k + "=" + v); });
    pairs.sort();
    return pairs.join("&");
  }

  function keyFor(rawUrl) {
    var u = new URL(rawUrl, location.href);
    var path = u.pathname.replace(/^\/api\//, "");
    var q = normQuery(u.search);
    return q ? path + "?" + q : path;
  }

  function searchLocal(q, limit) {
    var term = String(q || "").trim().toUpperCase();
    if (!term) return jsonResponse([], 200);
    return getSearchIndex().then(function (rows) {
      var raw = String(q || "").trim();
      var hit = rows.filter(function (r) {
        var sym = String(r.symbol || "").toUpperCase();
        var nm = String(r.name || "");
        var cn = String(r.name_cn || "");
        return sym.indexOf(term) === 0 || sym.indexOf("." + term) > -1 || nm.indexOf(raw) > -1 || cn.indexOf(raw) > -1;
      });
      hit.sort(function (a, b) { return (b.tradable ? 1 : 0) - (a.tradable ? 1 : 0); });
      return hit.slice(0, limit || 12).map(function (r) {
        return {
          symbol: r.symbol,
          shortname: r.shortname || r.name || "",
          market: r.market,
          market_name: r.market_name || r.market,
          tradable: !!r.tradable,
          held: false
        };
      });
    }).then(function (rows) { return jsonResponse(rows, 200); });
  }

  window.fetch = function (input, init) {
    var url = typeof input === "string" ? input : (input && input.url) || "";
    var method = ((init && init.method) || (input && input.method) || "GET").toUpperCase();
    if (url.indexOf("/api/") === -1) return origFetch(input, init);

    if (method !== "GET") {
      return jsonResponse({
        detail: { message: "静态快照只读：新增 / 修改 / 删除 / 模拟请在本机 uvicorn 服务上操作" }
      }, 501);
    }

    var key = keyFor(url);
    if (key.indexOf("search?") === 0) {
      var sp = new URLSearchParams(new URL(url, location.href).search);
      return searchLocal(sp.get("q") || "", Number(sp.get("limit") || 12));
    }

    return getIndex().then(function (idx) {
      var file = idx[key];
      if (!file && key.indexOf("screen?") === 0) file = idx["screen"];
      if (!file) {
        return jsonResponse({
          detail: { message: "静态快照未包含该数据：" + key + "（完整功能请在本地服务查询）" }
        }, 404);
      }
      return origFetch(withV(file), { cache: "force-cache" }).then(function (r) {
        return r.text().then(function (body) {
          return new Response(body, {
            status: r.status,
            headers: { "Content-Type": "application/json; charset=utf-8" }
          });
        });
      });
    });
  };

  function stamp() {
    if (!metaPromise) metaPromise = loadJson("snapshot-meta.json").catch(function () { return {}; });
    return metaPromise;
  }

  function badge() {
    if (document.getElementById("stSnapshotBadge")) return;
    var el = document.createElement("div");
    el.id = "stSnapshotBadge";
    el.style.cssText = [
      "position:fixed", "right:14px", "bottom:14px", "z-index:9999",
      "background:#111826", "border:1px solid #2b3a55", "border-radius:10px",
      "padding:8px 12px", "font:12px/1.5 -apple-system,'Segoe UI',sans-serif",
      "color:#9fb0c8", "box-shadow:0 6px 20px rgba(0,0,0,.35)", "max-width:320px"
    ].join(";");
    el.textContent = "静态快照 · 载入中…";
    document.body.appendChild(el);
    stamp().then(function (m) {
      var t = String(m.generated_at || "").replace("T", " ").slice(0, 16);
      el.innerHTML = "<b style='color:#c7d2fe'>静态快照</b> · 数据截至 " + (t || "未知") +
        "<br><span style='color:#8b98ab'>只读：编辑与实时行情请在本地服务启动后使用</span>";
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", badge);
  } else {
    badge();
  }
})();
