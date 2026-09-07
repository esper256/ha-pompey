/* Pompey debug: keep engine Web UIs under Home Assistant Ingress.
   Root-absolute /api and /Content calls would otherwise leave the
   /api/hassio_ingress/<token>/ prefix. Loaded first via nginx sub_filter. */
(function () {
  function mountPrefix() {
    var path = window.location.pathname || "";
    var match = path.match(/^(.*\/debug\/(?:radarr|sonarr|qbittorrent))(?:\/|$)/);
    return match ? match[1] : "";
  }

  var base = mountPrefix();
  if (!base) return;

  function rewrite(url) {
    if (typeof url !== "string" || !url) return url;
    if (url.indexOf(base) === 0) return url;
    if (url.charAt(0) === "#" || url.indexOf("mailto:") === 0) return url;
    if (url.indexOf("//") === 0 || /^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(url)) {
      try {
        var parsed = new URL(url, window.location.href);
        if (parsed.origin === window.location.origin && parsed.pathname.charAt(0) === "/") {
          if (parsed.pathname.indexOf(base) !== 0) {
            parsed.pathname = base + parsed.pathname;
            return parsed.toString();
          }
        }
      } catch (_exc) {}
      return url;
    }
    if (url.charAt(0) === "/") return base + url;
    return url;
  }

  var origFetch = window.fetch;
  if (origFetch) {
    window.fetch = function (input, init) {
      if (typeof input === "string") {
        input = rewrite(input);
      } else if (input && typeof Request !== "undefined" && input instanceof Request) {
        input = new Request(rewrite(input.url), input);
      }
      return origFetch.call(this, input, init);
    };
  }

  if (window.XMLHttpRequest) {
    var origOpen = window.XMLHttpRequest.prototype.open;
    window.XMLHttpRequest.prototype.open = function (method, url) {
      arguments[1] = rewrite(url);
      return origOpen.apply(this, arguments);
    };
  }

  function patchHistory(name) {
    var orig = window.history && window.history[name];
    if (!orig) return;
    window.history[name] = function (state, title, url) {
      if (url != null) arguments[2] = rewrite(String(url));
      return orig.apply(this, arguments);
    };
  }
  patchHistory("pushState");
  patchHistory("replaceState");

  if (window.WebSocket) {
    var OrigWS = window.WebSocket;
    function WrappedWS(url, protocols) {
      var next = rewrite(url);
      if (typeof next === "string" && next.charAt(0) === "/") {
        next = (window.location.protocol === "https:" ? "wss:" : "ws:") +
          "//" + window.location.host + next;
      }
      if (protocols === undefined) return new OrigWS(next);
      return new OrigWS(next, protocols);
    }
    WrappedWS.prototype = OrigWS.prototype;
    WrappedWS.CONNECTING = OrigWS.CONNECTING;
    WrappedWS.OPEN = OrigWS.OPEN;
    WrappedWS.CLOSING = OrigWS.CLOSING;
    WrappedWS.CLOSED = OrigWS.CLOSED;
    window.WebSocket = WrappedWS;
  }

  if (window.EventSource) {
    var OrigES = window.EventSource;
    function WrappedES(url, config) {
      return new OrigES(rewrite(url), config);
    }
    WrappedES.prototype = OrigES.prototype;
    window.EventSource = WrappedES;
  }

  // Webpack jsonp does script.src = publicPath + "640-<hash>.js". A
  // MutationObserver is too late: the browser already requested "/640-….js"
  // from the Home Assistant host (text/plain 404). Intercept the setter.
  function patchUrlProp(ctor, prop) {
    if (!ctor || !ctor.prototype) return;
    var proto = ctor.prototype;
    var desc = Object.getOwnPropertyDescriptor(proto, prop);
    if (!desc || !desc.set) {
      proto = Object.getPrototypeOf(proto);
      desc = proto && Object.getOwnPropertyDescriptor(proto, prop);
    }
    if (!desc || !desc.set) return;
    Object.defineProperty(proto, prop, {
      configurable: true,
      enumerable: desc.enumerable,
      get: desc.get,
      set: function (value) {
        desc.set.call(this, rewrite(String(value == null ? "" : value)));
      },
    });
  }
  patchUrlProp(window.HTMLScriptElement, "src");
  patchUrlProp(window.HTMLLinkElement, "href");

  var origSetAttribute = Element.prototype.setAttribute;
  Element.prototype.setAttribute = function (name, value) {
    if ((name === "src" || name === "href") && value != null) {
      value = rewrite(String(value));
    }
    return origSetAttribute.call(this, name, value);
  };

  function fixEl(el) {
    if (!el || !el.getAttribute) return;
    ["src", "href"].forEach(function (attr) {
      var value = el.getAttribute(attr);
      if (!value) return;
      var next = rewrite(value);
      if (next !== value) el.setAttribute(attr, next);
    });
  }

  if (document.documentElement && window.MutationObserver) {
    new MutationObserver(function (records) {
      records.forEach(function (record) {
        record.addedNodes.forEach(function (node) {
          if (node.nodeType !== 1) return;
          fixEl(node);
          if (node.querySelectorAll) {
            node.querySelectorAll("[src],[href]").forEach(fixEl);
          }
        });
      });
    }).observe(document.documentElement, { childList: true, subtree: true });
  }
})();
