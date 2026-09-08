// 一个接近真实业务代码的脚本：类、递归、闭包、循环、Map/Set、嵌套对象、
// 异步、try/catch。用来压测调试器在真实场景下的表现。

class Cart {
  constructor(taxRate) {
    this.taxRate = taxRate;
    this.items = [];
    this.index = new Map();
  }

  add(name, price, qty) {
    const line = { name: name, price: price, qty: qty };
    this.items.push(line);
    this.index.set(name, line);
    return this;
  }

  lineTotal(line) {
    return line.price * line.qty;
  }

  subtotal() {
    let sum = 0;
    for (let i = 0; i < this.items.length; i++) {
      const line = this.items[i];
      const amount = this.lineTotal(line);
      sum += amount;
    }
    return sum;
  }

  total() {
    const base = this.subtotal();
    const withTax = base * (1 + this.taxRate);
    return Math.round(withTax * 100) / 100;
  }
}

function factorial(n) {
  if (n <= 1) {
    return 1;
  }
  return n * factorial(n - 1);
}

function makeCounter(start) {
  let count = start;
  return function () {
    count += 1;
    return count;
  };
}

function parseConfig(raw) {
  try {
    const parsed = JSON.parse(raw);
    return parsed.value;
  } catch (err) {
    const fallback = -1;
    return fallback;
  }
}

async function fetchLater(value, delay) {
  await new Promise(function (resolve) {
    setTimeout(resolve, delay);
  });
  const doubled = value * 2;
  return doubled;
}

window.buildCart = function () {
  const cart = new Cart(0.08);
  cart.add("apple", 3, 4).add("pear", 5, 2).add("kiwi", 8, 1);
  const seen = new Set(cart.items.map(function (l) { return l.name; }));
  const total = cart.total();
  document.getElementById("out").textContent = "total=" + total;
  return { total: total, count: seen.size };
};

window.runFactorial = function (n) {
  return factorial(n);
};

window.runCounter = function () {
  const next = makeCounter(10);
  return next() + next() + next();
};

window.runParse = function (raw) {
  return parseConfig(raw);
};

window.runAsync = function (value) {
  return fetchLater(value, 20);
};

window.crash = function () {
  const data = { user: { profile: null } };
  return data.user.profile.name;
};

// 供「值检查补齐」测试：Map/Set、getter、长字符串、Promise、函数
window.buildProbe = function () {
  const probe = {
    map: new Map([["alpha", 1], ["beta", 2]]),
    set: new Set(["x", "y", "z"]),
    longText: "L".repeat(20000),
    promise: Promise.resolve(99),
    double: function (n) { return n * 2; },
    // 监视点只能作用在已存在、可配置的数据属性上
    hits: 0,
  };
  Object.defineProperty(probe, "computed", {
    enumerable: true,
    get: function () { return "from-getter"; },
  });
  Object.defineProperty(probe, "boom", {
    enumerable: true,
    get: function () { throw new TypeError("getter failed"); },
  });
  window.probe = probe;
  return inspectProbe(probe);
};

function inspectProbe(probe) {
  const marker = "inspect-here";
  return probe.map.size + probe.set.size;
}

// 供「事件断点」测试：点击按钮会走到这里
window.clickCount = 0;
function onDemoClick(event) {
  window.clickCount += 1;
  document.getElementById("out").textContent = "clicked=" + window.clickCount;
}
document.addEventListener("DOMContentLoaded", function () {
  const btn = document.getElementById("demo-btn");
  if (btn) {
    btn.addEventListener("click", onDemoClick);
  }
});

// 供「XHR 断点」测试
window.fireRequest = function (url) {
  return fetch(url).then(function (r) { return r.status; });
};
