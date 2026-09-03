'use strict';

(function (root) {
  var WIDTH = 1080;
  var HEIGHT = 1920;
  var VERSION = '3';
  var FONT = "DejaVu Sans, Noto Sans, Segoe UI, Arial, sans-serif";
  var LOGO_SIZE = 56;
  var LOGO_GAP = 16;
  var LOGO_DATA_URI = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAAWSElEQVR42uWbeZyU1Znvv+fdau2u6hUaaPYGQVpAjYqOkkSDxuidcQVsxKghSBJHEyeRqOgk49Uxyb2aTQWNwR0VxQ00JDqXRFzimJiIKMgO9kbT1VXdtb7ve879462q7qYXFuFm5ub9wAdoTp06z/57fs95hZRS8Xf8aH/Pwou/dwUowPh/+WWioHkhBlikUPm19Fh/NB/j6LuZIP8LACkljnRxlUIp1e2KQkPXNHQheilI5ZUi/rsooHDYghCO65K2c6SdHFnHxnFdXCWLgqEUCIGGQNM0TF3H0g38pkXAtLB0A3EUFSGOdBUQQqCUIpnLksikSNlZXCm7D1+wfB9pPMsLIcirBl3T8BsWpf4AYcuPpmlFr/kvpQAFaHmLJzJpYqkuMk4OhUITGgKQrkRoAr/fj+Wz8sJ0K01KSTabJZvOoPLCKxQyb3mfbhANhIgGQkUl/5dRgBCCjGOztytOMpdBIDyFCOEJDpRESnFsmy2fbGHjBx+ya/sOEvEEmqZRGokwaswoJtcfy9i6cWiaTmcigSY0EALyipBKETBNqsIRQpb/iCjhMytACEF7qpO2ZCdKeRYv5HHXcQkEA+iGzm9efpUVDz/OX//0PvF4HOnKYggoqdB0nWhZhGknHM8VC67kC2efRVentyd57xKAKxUIKA+EqQqXIhAo5GFnh8NWgEAgUTQnYsQzKQytG1JIKZFSUlZezvat27j95h/w2qtr0TSNQDCIYRj9lkDHdUglUyiluGrRAm687WayuSz9ZT9XSoKWj2Gl5Zi6ftjecFgKEELgSMmn8X2kc1l0TS9aXSmFz+cjGArxykurufWG79PU2ES0rAylFFLKfpBBd+XXdQ0QtDY38r1bb+Hb3/8X2ttjGKbRB8U5UmLpBiOiFfgM87CUcMgK8IR32dOxj4xjo+ddvvDdPp/Frh27eOxXD/PE8kfRdR1/wI9jO73RTUH+oh7y/1AgNC93WD4fz7+2muG1I0jE4ximWfwI+chwlcTQdGqjlfgME6mkhz2OFg6QStEYb+8lvJSSUDjMU48+yS9+cg+pZIqOWIzSSKmnsILw/ShT0zVQIJUXxyqfExQKpSSd+2I06zrDa0fQmUigemhRAbrQit44MlrledAhmFQ7VOs3J2Kkcll0UbRFUcjZl89l9vzL0IQgEo0U80Gfmgnohk42k6W1pYX29hi5nE02m8N1HHx+H7F9bSy6ZiF/Wv8OZ550Gst+di9+v797E0EPJQhyjkNjov2QhD8kDxBCEEt1kcik+sS8aZo0NTbyzhtvkYjFQQhcV+bRYB4Hq25lKWDv3jYmT5rE3LmzOWbiRDricTZs2MCaNa+wedNH1B83nfPPP49//McLAY1/vfEWEokEN9x8I4l4HE3Xe+lU1zSSuSxtyQRV4chB54ODygFCCLKOzc7Y3j7RpZTCME1i7e1ccOZ57N65lcrqYX0PoBRC03AcBzuXY8mSm5kz+1JWr1nDihVP89HHm3Bsm3BJmKn19dx1150sufUHvPDii1RXV+M4NrlsjqfXrGJS/WTSqTRC0/okRqkUtdFKgpbvoJRw0B7Q2hVHKdWnkxNCkMvmGDJ0KI+sepIFc77KvrY29Hyp87K6hwQd10VJyTPPrOCYiRO5+mtf55NPtlBXN555DZcxffpUZpxyMnV1dQDMmnUWv1m7Ftdx0HWdVCrFutf+g2knHk+yK4muaf2WyNauOKPKqo5MCAgh6MykSWYzvVy/VyLRNVLJFLWjRlIajdDc1IRpmuRsm1h7AoBINEoqmeThhx/iy+eczfbtO/j1Qw9QW1vbZz/XdZFScs3CBbz++n/wwosvUVFejqZp7N65O59XRLF49CwomhBknBzxTIpoIHTAqnBABSilaE93DtzD593bMAza22N0tLd7wudyDK0Zynf/5duUl1ewbNmD1NdPYfall5DL5RgzZnQfgUGgaV5XKKXXMV5y8UWsWvWCV/MUeWTY2+hivz81vHxV6gsOfu4DVQEhBF3ZDOlcrhfEHejRdQ2h6V5vkMkwoa6Om76/mLlzLiUcDnHnHbcjpUTX9SJalFKiaRqGYWAYOlo+rjVNQwjB+PHjCIaCSOkilaRqSBWa0Iod42A5qzObLibdwy6D8UxyP7TWPzCWUhIIBAiFgkgpcaWkuroKx3HYu7eNpfffy7BhNV6/kBdO7Ed+FHCG7JG8IpFS/D4f0pXomsa4uvH55CYOGLqJTOqAzJI22AY5xyFt57xkM+iX5bF5KERldRWu66KUYtrUqei6ztixY5g06Zi8l+i4+2MDCkDIK2e6phUzellZOeGSMJlslpJIhElTJpPLZtE0UaTPVO9o9DgWBGk7R9a2Bw2DQSVL5jK4Sh4cJyAlhmkwZtxYMukM5eXlzJr1JQ/taRpNiQ42NO4ink1j5Gu4ypMjUin0fOhsbm1i895mNF1HE4KysiifO/FEkp1xxo4bS+2okWSz2V5CiQGMIpWkK5fJ+686HAVkD4irCxaQUhLwB5h+8onYdopjp0xm9JjRZOwcNzy8lM9dMZeZDXM5/soG7l69qqgYLW/xP277hDO+dy2nzpvDaQ2z+cLi6/jT9q0AzDhtBlI6nHz6DIZUD8W27V4tch8w0MOLU3a2m5s82CogELhSknXsQcUvRKKUknBJCW+s+wNrX1iN5QtyfP1xmIbBtffdw9I7f0x5p40mBO3vb+Y7f92AzzKZfcoZpDIZciguWPwd2l5/m1Lp2eQP733IP+3YyScrVlE/fgKmFeCjv2xg7auvcso/zCDZlSpijD7yi245co6NKyX6AMmwXyQohBc/u2N7ezO0g5RBn9/PN6/4Oj+85RYAFv37vzFl1kyef/pZxLsfIXymV6cVSOmif+EETpg6jaBmsKWthW0v/I5gRxLH0EGAkXPorChh5tWXkf20hZsubqBu5Ciuue46fvqre4shxH6hsD+lLpViZLSKgGX1iwy1/u0Ktuv0ysaq6O+qXwzQtreNqooKTMvkw40bufuWW6kqiZDd1+HVdQVIhRSABLc9wY+vWsTKm35IdSCMk+hC6hoo6a3TNUQyTTKeYPHVC8im0qSyGWqHD6eluQXTNAd3zR44JiedAQ2o9T8wAtt1+9lP9Q581V0CQ+EQqUyakbW1/O6115k2aizXn3chKugHqTxD5elvR7qU1QyhtrwSgGPGjSOjCXTpWVQJLzaTGpx/2ul8adqJPPHkCqoqK+lMJikpLcEtnE/1PhY90GFBFqcoizr4JNhfqcoz/n0yj+M4RKNRTJ9Fc1Mz8+fPY+PWLQwNl3L2l89mrw+0nIPmSlQ2R7w8yJyvnE+pP4BSisu/MAt//Xi6XBvddjFslw7XpvKEKcw5/Yt88P5fOe3UGdi2jS1dKiorsW2n2/3FQGbMn0+6A9YL/bbbbvvXfvF/Nk3GsYt0d3cfLnrpAuG5WSgc5tNPP2XTBxu5/PIGomVRdF3nzOknskXYfNyxl2zARIyvZcHCr3P7vKs8QlMpRpRXMmnSMbwVa2KfmyUXDTNy5ik8eNOtTB81lpxjc/JJn2PV8y+gBX2c/vnTSSaTHmrc7zgAojB2EAKlwGeYlPgCB98OCyFoSrQTT6eKIEj1pu56JZxCHkh0dvK9Rdfz7IonKCkt7bXn+7u209wRY+zQGiZU1/QiTwskaTyT4r3tWxECThozgZDPh1SqaIRzz/sf3HzXDxk+fDjZXM5Dkv2Hfq9qFgkEqSkt6zcJDqiA5kSMjnQyP6Do+w1C9C6FrusSLS/jqUdXsG71WhYsuIoZM2ZQGilF5fF/T9a4s7OTSMRjjTo6OigpKem1phCGmXSat99+h+XLH2HccZNZdN03aW9v99YeABGL/B7RQIihpWVIpfosNwaq7dr+8FENEGDFRkinI9bBRXMuZkjNEH5w5x18a+E1zGu4DDufT1QBMRoGz7/wEr9f93uGjxhOJpPhrn+/w/OIvJWUlJimyZtvvs3iJUtY9O1rmXXu2XTEYl5brnozy6IHFN7/nINBYWMgeOP1/vttprqXKNGX1NY0jUQ8wXnnno+Uij+vW8+8hsuK/5f/C67jcsX8eQwfVsOWrdv42tVX4uRJj0LIOfl2+M9//jNXf+PrXD57Llsbd+bDRe0X9YO3akZRlr4uo/U7zu71oUMkGQ2d1lgrw0YMo7GlBZTq49oFjv+ss87kmoULMAwD0zS7ldSjHd62Ywc1w4fRnNjXiwccuAno+wMz/7n+4PCAhIil60XXEf1iAa9e99eF2DmbcXXjSGezbN78CXUT6pBSFtvf1avXsG7d79m1eze27TB06FDq649lXsNlhEKh4tqOWAeNzc1MOnYSuXwD1J2H825IbzTYp85rAks3DpERUmAZBoam40q3/xgSA6MwpSSWaXHaF2dyz09/zr33/hzXdYvoberU4xg5ciSBgIcDMpkMls/C5/MVGSLLsrh/6TKOmTqFispK2vbuRdf1/ZxY9eXE9mOzdE3H1I2+CHYwIKTwPmjphjeg6BNkov+WsKh1nVh7Ow3zG4jF4zz++JNYloVj2wCMGDGC+voprF//Ji++9DLjx49n4oQJGIaB4zhYlsUb69/kjfVvsuhbi4jFYsUwEj0BSE/D9K2B3ljdMHtXsoPxgIJCA6ZFVy6DIejh7qLPhGugJ5PJcN+9P2f2pZdRUVnBOWfPIpfLFev+WV86k1w2h2WZHouU95KNGzdyy5Lb+PVDD+D3+0glEphGP1bsR+jCzwqESsC0Bkh/g7bD3hOy/OxLdR6oAvY5kMrPACxdJ1oaYfnyB5nbMB9daHxp1lnYeZZm+LBhRShdcPuPP/6Ya77xLX52z92MGT2apkQ7Zp5D1ITob6jY5/sLjqEJjZDlG/Tc2mBssN808eenrmIw4UVvakvXNSzDYPlvX2VfvIOamhoeeeTX3LjkVh5/7AlM0+w1IjcMw3P7dX+g4atX8ZMf/4hp06aSy9k8+ru1ZB0Hv2X16E8GQUB5QkAqhc80Dzg1HnQyJISgPdlJS1e81/y/Py8soC5D1zENg8W/WsYLb/2BLcsepyZajtAEm7ZvY+aXz2Xm9BOYfeFFDB06BE3TaG9v5+VXXmHl2t+w8onHOOOkk3Ecr9mpW3g5tdXVPHD9d7EMk0wu240C6T/5CSFwlWRISZSyQOgzKCAPSHbEWpGFlrbfuFc4jotlmuiazg3L7uXFt96gJOjntdvv5vhxdWRzWXyWj79s3cwpsy8is6eJQIkHhdOpJJSG+T8rnmbm1OOLa1s7Ophx47fY3tTIqcdO4f5/voGwL0Ayk8YwjO7q1E+M6kJjdHk1+gEodO1AIW3oOhF/sEiOqv3IENd1EQjKo1EMw+CGZb/kxbfeoLosSlcmy6f79ubBjw8FTB03gbdWrGT4ydNJV4XJVJdQXj+B9c8+y8ypxyMBKx+3LR0x2hJxqsvK+OOmj1n0s/9NKpelqrwCPY8oPeuq4sRY5PmJSCBUvGj1mcbjSinKgmEvEeVzgcoLrus6FdEoCsEzq1/m4u99h1f/84+Ul5TgSokrXXa2NgEQ2/IcjW/cSLLtQ6aNn8jrv3iYSSPGMaxsCL/92YOcOnkq6fgOWt65nZa/3AfA7rYW0rkcUirKwmH+tHULFy6+gWVPPEYqnaairAzTMIs0vHdeiWUYB3T9QxqOGppOZaiUpkTMuwJjWYSCQVra2ljx8ks89uLzfPzhh1hjRhGpqvSGmcKr21ubG1FK0dX4NrEP7iO+bTWhUbOYMOMmXr/nf5LOdDFmTC173v43Orc+Rza+ndCosxly3DXsaG3GcR3v/oHrEPL72d0RY8lP7mLpU08y+yvncem55zF6xAjSmQyZbBYFVARL0TWt3+7vsBSglCIaCNGZSSNMnabWVu577FGeemU1u3buAMMgWF1FsKIC1+lmanRNZ0dLsxerMosRqEChEd/0FBub3sbSdVAOH70XwOnYjGZFMAJVKOkJvbO1tRfP5TgOwZIStJoa9rS08L/u/yXLn1vJRbPOYd4FFzKmdiQ4Xv+vDkL4QxqPSympiZRx4y/u5qGnn6QjFgOfD38kgmvbaKEgSqhevXw4EOSNv77PlsZWov4AHY6NbmkY/krs+A6yUoHQ0ITEDFaD0HDtJD7TpDOVZs076wkHArh5SkvkUaoIBTE7O9FDQWJdXSx7/FEefW4lX71kNj//7s3FXuKIXpGRSmFoOidPPJaOjhjBSAS/z4frOAhNx4qUepYvZmaFqevs60rw/ft/ienzbm042QROpp3yYxqonNxAxYQLqapfiLSTOJkYjmsTjQzhzuXL2bBtC0GfHyVV8U4xQmCVhtHy0No0TYKRCOl0ms9POxFd03ux2Uf0llihzi977hkW3vxdfJFInilXmOEwZkkI3e9HGHoetSp0TbB3WytLL67mnKGbyEU+x5D6qyip/XyvvVMt79H6wUOo1tfZlKnjguUxAsNKEboJKI8/lBI3m8Pu6sLp7ALp0WWZWDu/vP0uvnHJXBzXPeAs8zNdk3NcF9Mw+Onjj3D9HbdhhUsQmoabswFFcFgNZp4G8y5DGST2NFNpt/Puo8upGX1ynvFxe4BahaYZKCAX387pV87jvaYUFXVjcB23eAnD6UqR3NPoUWyWCUqRjcW4c/ESFl+5ANtxinPHo3JLjDwusB2H6xrm88DtP8LJZslls/iCAUIjhmFGSjzhRXfoBEoCfNqa4Z/vX+XNHOyc19cJDYSGEDq2nUMAtz22hnc3fEpJpAS3+zY1SiqMcIjgyOFY4RB2Lke2q4u7b72dxVcuwDkM4Q9LAT2V8LULLmHNvQ8xNFJGsqsTf1lZD9alwJkppKbjKyth5arHeeC5pzFNC6fH3MF1JaZpsfat9dy19G6sSAhlmsW9RA/gFYhGSGXSRHwBVv70Pq5vmF+k0w7n+UyXpQvhsLO5iW/+cAmr//IeJSNHEDDM4kUHIQTStknu2oNyHCyh8c4Tz3Ls+DpvuiO8m+Ut7fs4Yc4FtOxrQzdNgiOGowf8qPwNEl3TyLgO8V17mDluIkt/cAcTR40+LLc/YgoosDeFzm75i6v40cvPsam1iZDlI1BgeJSkc8duhOOQTSWZPnkKbz78FKZheNfsDIOvXLuQNa+txR+N4kpJ6ZhRaIb3tkjGztGZyTCqrIJvzzqfa+c0eODoM1j+iCmgEOeiwAonkzz42zU89NtX2Ny4GyEEoUAAu3kvdlcXpuWjq62Vb17xNX6xeAkAdz60jJt+fAfhykoc20bz+bCGDSWVzWC7LmOG1DD/i7O45pzzGRLtvnStaZ/9pbcj+spMT2/oSqd5+d23eGb9Ov64ZRN7PtkCTc1gWd7vRJyVSx+muryCMxouBJ8PHAeyWSgvZ+jkSUwfPZaLTj2Dfzr5H6jIT5qOhNWPmgIKsFkqidGDiW1s38e7Gzfw9n++y4fbtrJnbwv72vfh8wcwdYNEZ5zKikqGVVYxecw4Tpp+PCfVH8eoqiE98o3jvX4jjuxrU+JovTqrikmQvkMWIJXJkEqnkUoSDoYI+nx96G1vUiSPiuBHXQH9eYVS3sitMPTosyY/GhOA0LS+47n/rgoYSCn701h/i8fgb/T8rQQ+Ikjw/6fn/wKJMLebbxgwowAAAABJRU5ErkJggg==';

  var PALETTE = {
    bg: '#F7F4EF',
    ink: '#1A1A1A',
    muted: '#5C6560',
    accent: '#2F6F4E',
    accentSoft: '#1F6F5E',
    cell: '#FFFFFF',
    line: '#D7D0C6',
    green: '#2F9E6B',
    yellow: '#C9A227',
    red: '#C45C4A',
    given: '#1F6F5E',
    givenFill: '#E4EFE8',
    empty: '#E7E1D8',
  };

  var KIND_COLORS = {
    ladder: '#2F6F4E',
    salad: '#2F6F4E',
    alphabetty: '#3B6EA5',
  };

  var cache = { key: '', blob: null };

  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&apos;');
  }

  function mulberry32(seed) {
    var a = seed >>> 0;
    return function () {
      a |= 0;
      a = (a + 0x6D2B79F5) | 0;
      var t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function wrapText(text, maxChars) {
    var words = String(text || '').split(/\s+/).filter(Boolean);
    var lines = [];
    var current = '';
    words.forEach(function (word) {
      var trial = current ? current + ' ' + word : word;
      if (trial.length <= maxChars) {
        current = trial;
      } else {
        if (current) lines.push(current);
        current = word;
      }
    });
    if (current) lines.push(current);
    return lines.length ? lines : [''];
  }

  function textLines(x, y, lines, attrs) {
    return lines.map(function (line, i) {
      return '<text x="' + x + '" y="' + (y + i * (attrs.lh || 48)) + '" ' + attrs.prop + '>' +
        esc(line) + '</text>';
    }).join('');
  }

  function roundedRect(x, y, w, h, r, fill, extra) {
    return '<rect x="' + x + '" y="' + y + '" width="' + w + '" height="' + h +
      '" rx="' + r + '" ry="' + r + '" fill="' + fill + '" ' + (extra || '') + '/>';
  }

  function stateFill(state) {
    if (state === 'green') return PALETTE.green;
    if (state === 'yellow') return PALETTE.yellow;
    if (state === 'red') return PALETTE.red;
    if (state === 'given') return PALETTE.givenFill;
    return PALETTE.empty;
  }

  function stateStroke(state) {
    if (state === 'given') return PALETTE.given;
    if (state === 'empty') return PALETTE.line;
    return 'none';
  }

  function saladFill(hintCount) {
    var n = Math.max(0, Number(hintCount) || 0);
    if (n <= 0) return PALETTE.green;
    if (n === 1) return PALETTE.yellow;
    if (n === 2) return '#D0893B';
    return PALETTE.red;
  }

  function kindAccent(kind) {
    return KIND_COLORS[kind] || PALETTE.accent;
  }

  function decoBackground(payload) {
    var kind = payload.kind || payload.game_kind;
    var accent = kindAccent(kind);
    var rng = mulberry32(Number(payload.seed) || 0);
    var parts = [
      '<rect width="' + WIDTH + '" height="' + HEIGHT + '" fill="' + PALETTE.bg + '"/>',
      '<rect width="' + WIDTH + '" height="10" fill="' + accent + '"/>',
    ];
    if (kind === 'ladder') {
      var i;
      for (i = 0; i < 7; i += 1) {
        var y = 280 + i * 210;
        var w = 420 + rng() * 280;
        var x = rng() < 0.5 ? -80 : WIDTH - w + 80;
        parts.push(roundedRect(x, y, w, 96, 18, accent, 'opacity="0.06"'));
      }
    } else if (kind === 'salad') {
      var i;
      for (i = 0; i < 6; i += 1) {
        var size = 90 + rng() * 140;
        var sx = rng() < 0.5 ? -40 : WIDTH - size + 40;
        var sy = 300 + rng() * 1180;
        parts.push(roundedRect(sx, sy, size, size, 22, accent, 'opacity="0.06"'));
      }
    } else {
      alphabettyDecor(parts, payload, rng, accent);
    }
    return parts.join('');
  }

  function alphabettyDecor(parts, payload, rng, accent) {
    var letters = 'АБВГДЕЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ';
    var variant = Number(payload.variant);
    if (!isFinite(variant)) variant = (Number(payload.seed) || 0) % 3;
    variant = ((variant % 3) + 3) % 3;
    var count = variant === 1 ? 12 : variant === 2 ? 20 : 9;
    var i;
    if (variant === 1) {
      var cx = WIDTH / 2;
      var cy = 980;
      var radius = 340;
      for (i = 0; i < count; i += 1) {
        var ang = (Math.PI * 2 * i) / count - Math.PI / 2;
        var ch = letters.charAt((Number(payload.seed) + i * 7) % letters.length);
        parts.push(
          '<text x="' + (cx + Math.cos(ang) * radius) + '" y="' + (cy + Math.sin(ang) * radius) +
          '" text-anchor="middle" font-family="' + FONT + '" font-size="92" font-weight="700" fill="' +
          accent + '" opacity="0.14">' + esc(ch) + '</text>'
        );
      }
      return;
    }
    if (variant === 2) {
      var cols = 5;
      var rows = 4;
      var startX = 140;
      var startY = 620;
      for (i = 0; i < cols * rows; i += 1) {
        var col = i % cols;
        var row = Math.floor(i / cols);
        var letter = letters.charAt((Number(payload.seed) + i * 3) % letters.length);
        parts.push(
          '<text x="' + (startX + col * 180) + '" y="' + (startY + row * 210) +
          '" font-family="' + FONT + '" font-size="120" font-weight="700" fill="' +
          accent + '" opacity="' + (0.07 + (i % 3) * 0.03) + '">' + esc(letter) + '</text>'
        );
      }
      return;
    }
    for (i = 0; i < count; i += 1) {
      var ch2 = letters.charAt((Number(payload.seed) + i * 11) % letters.length);
      var x = 80 + rng() * 880;
      var y = 520 + rng() * 980;
      var size = 140 + rng() * 180;
      parts.push(
        '<text x="' + x + '" y="' + y + '" font-family="' + FONT + '" font-size="' + size +
        '" font-weight="700" fill="' + accent + '" opacity="' + (0.06 + rng() * 0.08) +
        '" transform="rotate(' + (rng() * 36 - 18) + ' ' + x + ' ' + y + ')">' +
        esc(ch2) + '</text>'
      );
    }
  }

  function headerBlock(payload, accent) {
    var dateLines = wrapText(payload.date_label || '', 28);
    var headlineLines = wrapText(payload.headline || '', 32);
    var y = 210;
    var parts = [];
    if (dateLines[0]) {
      parts.push(textLines(WIDTH / 2, y, dateLines, {
        lh: 42,
        prop: 'text-anchor="middle" font-family="' + FONT + '" font-size="32" fill="' + PALETTE.muted + '"',
      }));
      y += dateLines.length * 42 + 40;
    } else {
      y += 24;
    }
    parts.push(textLines(WIDTH / 2, y, headlineLines, {
      lh: 70,
      prop: 'text-anchor="middle" font-family="' + FONT + '" font-size="54" font-weight="700" fill="' + accent + '"',
    }));
    y += headlineLines.length * 70 + 28;
    return { svg: parts.join(''), bottom: y };
  }

  function footerBlock(payload) {
    var statsLines = wrapText(payload.stats_line || '', 34);
    var y = 1588;
    var parts = [];
    if (statsLines[0]) {
      parts.push(textLines(WIDTH / 2, y, statsLines, {
        lh: 40,
        prop: 'text-anchor="middle" font-family="' + FONT + '" font-size="30" fill="' + PALETTE.muted + '"',
      }));
      y += statsLines.length * 40 + 36;
    }
    var brand = payload.brand || 'interoves.com';
    var textW = Math.max(200, brand.length * 21);
    var groupW = LOGO_SIZE + LOGO_GAP + textW;
    var x0 = (WIDTH - groupW) / 2;
    var logoY = 1720;
    if (LOGO_DATA_URI) {
      parts.push(
        '<image href="' + LOGO_DATA_URI + '" x="' + x0 + '" y="' + logoY +
        '" width="' + LOGO_SIZE + '" height="' + LOGO_SIZE + '"/>'
      );
    }
    parts.push(
      '<text x="' + (x0 + LOGO_SIZE + LOGO_GAP) + '" y="' + (logoY + 38) +
      '" text-anchor="start" font-family="' + FONT + '" font-size="34" font-weight="700" fill="' +
      PALETTE.ink + '">' + esc(brand) + '</text>'
    );
    return parts.join('');
  }

  function ladderVisual(payload, top, bottom) {
    var steps = payload.steps || [];
    if (!steps.length) return '';
    var areaH = Math.max(240, bottom - top);
    var gap = steps.length > 10 ? 8 : 12;
    var stepH = Math.min(78, Math.floor((areaH - gap * (steps.length - 1)) / steps.length));
    stepH = Math.max(36, stepH);
    var maxLen = 1;
    steps.forEach(function (step) {
      maxLen = Math.max(maxLen, Number(step.length) || 1);
    });
    var minW = 280;
    var maxW = 820;
    var totalH = steps.length * stepH + (steps.length - 1) * gap;
    var y0 = top + Math.max(0, (areaH - totalH) / 2);
    var parts = [];
    steps.forEach(function (step, i) {
      var t = (Number(step.length) || 1) / maxLen;
      var w = Math.round(minW + (maxW - minW) * t);
      var x = (WIDTH - w) / 2;
      var y = y0 + i * (stepH + gap);
      var fill = stateFill(step.state);
      var stroke = stateStroke(step.state);
      var extra = stroke === 'none' ? '' : 'stroke="' + stroke + '" stroke-width="5"';
      parts.push(roundedRect(x, y, w, stepH, Math.min(16, stepH / 3), fill, extra));
      if (step.label) {
        var label = String(step.label);
        var fontSize = Math.min(stepH * 0.52, w / Math.max(1, label.length * 0.62));
        fontSize = Math.max(16, fontSize);
        var textFill = step.state === 'given' ? PALETTE.given : PALETTE.ink;
        parts.push(
          '<text x="' + (x + w / 2) + '" y="' + (y + stepH * 0.68) +
          '" text-anchor="middle" font-family="' + FONT + '" font-size="' + fontSize.toFixed(1) +
          '" font-weight="700" fill="' + textFill + '">' + esc(label) + '</text>'
        );
      }
    });
    return parts.join('');
  }

  function saladVisual(payload, top, bottom) {
    var letters = payload.grid || [];
    var results = payload.word_results || [];
    var count = results.length || Number(payload.word_count) || 0;
    if (!letters.length && !count) return '';
    var areaH = Math.max(240, bottom - top);
    var cell = 148;
    var gap = 16;
    var gridSize = cell * 4 + gap * 3;
    var tileGap = 12;
    var tile = count
      ? Math.min(64, Math.floor((820 - tileGap * Math.max(0, count - 1)) / Math.max(1, count)))
      : 0;
    tile = count ? Math.max(24, tile) : 0;
    var between = letters.length && count ? 40 : 0;
    var totalH = (letters.length ? gridSize : 0) + between + (count ? tile : 0);
    var y0 = top + Math.max(0, (areaH - totalH) / 2);
    var parts = [];
    var r;
    var c;
    if (letters.length) {
      var gx = (WIDTH - gridSize) / 2;
      for (r = 0; r < 4; r += 1) {
        for (c = 0; c < 4; c += 1) {
          var cx = gx + c * (cell + gap);
          var cy = y0 + r * (cell + gap);
          var letter = String(letters[r * 4 + c] || '');
          parts.push(roundedRect(
            cx,
            cy,
            cell,
            cell,
            24,
            PALETTE.cell,
            'stroke="' + PALETTE.line + '" stroke-width="4"'
          ));
          if (letter) {
            parts.push(
              '<text x="' + (cx + cell / 2) + '" y="' + (cy + cell * 0.68) +
              '" text-anchor="middle" font-family="' + FONT + '" font-size="' +
              Math.round(cell * 0.48) + '" font-weight="700" fill="' + PALETTE.ink + '">' +
              esc(letter) + '</text>'
            );
          }
        }
      }
    }
    if (count) {
      var rowW = count * tile + (count - 1) * tileGap;
      var tx = (WIDTH - rowW) / 2;
      var ty = y0 + (letters.length ? gridSize + between : 0);
      var i;
      for (i = 0; i < count; i += 1) {
        var item = results[i] || { hint_count: 0 };
        parts.push(roundedRect(
          tx + i * (tile + tileGap),
          ty,
          tile,
          tile,
          tile * 0.28,
          saladFill(item.hint_count),
          ''
        ));
      }
    }
    return parts.join('');
  }

  function attemptsWord(payload) {
    if (payload.attempts_word) return String(payload.attempts_word);
    var n = Math.max(0, Number(payload.attempts) || 0);
    if ((payload.locale || 'ru') === 'en') return n === 1 ? 'try' : 'tries';
    var n10 = n % 10;
    var n100 = n % 100;
    if (n10 === 1 && n100 !== 11) return 'попытка';
    if (n10 >= 2 && n10 <= 4 && n100 !== 12 && n100 !== 13 && n100 !== 14) return 'попытки';
    return 'попыток';
  }

  function alphabettyVisual(payload, top, bottom) {
    var accent = kindAccent(payload.kind || 'alphabetty');
    var cy = (top + bottom) / 2;
    if (payload.attempts == null || payload.attempts === '') return '';
    var count = String(payload.attempts);
    var word = attemptsWord(payload);
    var fontSize = count.length > 3 ? 56 : 84;
    var wordSize = 32;
    return (
      '<circle cx="' + (WIDTH / 2) + '" cy="' + cy + '" r="176" fill="none" stroke="' +
      accent + '" stroke-width="10" opacity="0.35"/>' +
      '<text x="' + (WIDTH / 2) + '" y="' + (cy - 6) +
      '" text-anchor="middle" font-family="' + FONT + '" font-size="' + fontSize +
      '" font-weight="700" fill="' + PALETTE.ink + '">' +
      esc(count) + '</text>' +
      '<text x="' + (WIDTH / 2) + '" y="' + (cy + 52) +
      '" text-anchor="middle" font-family="' + FONT + '" font-size="' + wordSize +
      '" font-weight="600" fill="' + PALETTE.muted + '">' +
      esc(word) + '</text>'
    );
  }

  function buildShareCardSvg(payload) {
    payload = payload || {};
    var kind = payload.kind || payload.game_kind || 'ladder';
    var accent = kindAccent(kind);
    var header = headerBlock(payload, accent);
    var visualTop = header.bottom + 24;
    var visualBottom = 1540;
    var visual = '';
    if (kind === 'ladder') visual = ladderVisual(payload, visualTop, visualBottom);
    else if (kind === 'salad') visual = saladVisual(payload, visualTop, visualBottom);
    else visual = alphabettyVisual(payload, visualTop, visualBottom);
    return (
      '<svg xmlns="http://www.w3.org/2000/svg" width="' + WIDTH + '" height="' + HEIGHT +
      '" viewBox="0 0 ' + WIDTH + ' ' + HEIGHT + '" role="img">' +
      decoBackground(payload) +
      header.svg +
      visual +
      footerBlock(payload) +
      '</svg>'
    );
  }

  function rasterizeSvgToPngBlob(svgText) {
    return new Promise(function (resolve, reject) {
      if (typeof Image === 'undefined' || typeof document === 'undefined') {
        reject(new Error('canvas-unavailable'));
        return;
      }
      var blob = new Blob([svgText], { type: 'image/svg+xml;charset=utf-8' });
      var url = URL.createObjectURL(blob);
      var img = new Image();
      img.onload = function () {
        try {
          var canvas = document.createElement('canvas');
          canvas.width = WIDTH;
          canvas.height = HEIGHT;
          var ctx = canvas.getContext('2d');
          if (!ctx) throw new Error('canvas-context');
          ctx.fillStyle = PALETTE.bg;
          ctx.fillRect(0, 0, WIDTH, HEIGHT);
          ctx.drawImage(img, 0, 0, WIDTH, HEIGHT);
          if (typeof canvas.toBlob === 'function') {
            canvas.toBlob(function (png) {
              URL.revokeObjectURL(url);
              if (!png) reject(new Error('png-failed'));
              else resolve(png);
            }, 'image/png');
            return;
          }
          var dataUrl = canvas.toDataURL('image/png');
          URL.revokeObjectURL(url);
          var comma = dataUrl.indexOf(',');
          var binary = atob(dataUrl.slice(comma + 1));
          var bytes = new Uint8Array(binary.length);
          var i;
          for (i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
          resolve(new Blob([bytes], { type: 'image/png' }));
        } catch (err) {
          URL.revokeObjectURL(url);
          reject(err);
        }
      };
      img.onerror = function () {
        URL.revokeObjectURL(url);
        reject(new Error('svg-image-failed'));
      };
      img.src = url;
    });
  }

  function payloadKey(payload) {
    try {
      return JSON.stringify(payload || {});
    } catch (err) {
      return String(Date.now());
    }
  }

  function renderShareCardPng(payload) {
    var key = payloadKey(payload);
    if (cache.blob && cache.key === key) {
      return Promise.resolve(cache.blob);
    }
    return rasterizeSvgToPngBlob(buildShareCardSvg(payload)).then(function (blob) {
      cache = { key: key, blob: blob };
      return blob;
    });
  }

  function resetCache() {
    cache = { key: '', blob: null };
  }

  var api = {
    WIDTH: WIDTH,
    HEIGHT: HEIGHT,
    VERSION: VERSION,
    PALETTE: PALETTE,
    buildShareCardSvg: buildShareCardSvg,
    rasterizeSvgToPngBlob: rasterizeSvgToPngBlob,
    renderShareCardPng: renderShareCardPng,
    resetCache: resetCache,
  };

  root.DailyShareCard = api;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
})(typeof window !== 'undefined' ? window : globalThis);
