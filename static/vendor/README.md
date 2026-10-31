# Vendored front-end dependencies

The site must render without third-party CDNs: `ru.interoves.com` is served to
visitors in Russia, where `cdn.jsdelivr.net` and similar hosts are not reliably
reachable. These files ship with the deployment bundle and are published by
`collectstatic` like any other app static file. Nothing is downloaded at runtime.

| Package | Version | Source | License |
|---|---|---|---|
| `@phosphor-icons/web` | 2.1.2 | `https://registry.npmjs.org/@phosphor-icons/web/-/web-2.1.2.tgz`, `src/regular` and `src/fill` | MIT, `phosphor-icons-web/2.1.2/LICENSE` |
| `chart.js` | 4.4.4 | `https://registry.npmjs.org/chart.js/-/chart.js-4.4.4.tgz`, `dist/chart.umd.js` | MIT, `chart.js/4.4.4/LICENSE.md` |

npm `dist.integrity` of the tarballs (verified on download):

- `@phosphor-icons/web@2.1.2`: `sha512-rPAR9o/bEcp4Cw4DEeZHXf+nlGCMNGkNDRizYHM47NLxz9vvEHp/Tt6FMK1NcWadzw/pFDPnRBGi/ofRya958A==`
- `chart.js@4.4.4`: `sha512-emICKGBABnxhMjUjlYRR12PmOXhJ2eJjEHL2/dZlWjxRAZT1D8xplLFq5M0tMQK8ja+wBS/tuVEJB5C6r7VxJA==`

Local modifications:

- Phosphor `style.css`: the `@font-face` `src` lists keep only the WOFF2 and
  WOFF files; the TTF and SVG fonts (about 6 MB) are not vendored. Every other
  line matches upstream.
- Chart.js: `dist/chart.umd.js` is already minified upstream. jsDelivr's
  `chart.umd.min.js` was a CDN-side re-minification of the same file.

`games.tests.test_frontend_assets` checks that the site templates do not load
CSS, JS or fonts from third-party CDNs and that vendored CSS references only
files that exist here.
