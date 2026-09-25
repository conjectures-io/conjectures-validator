# Competition standings dashboard

A single static page (`index.html`) showing the lz77 competition from the public
`/v1/competitions` API: the Pareto chart (miners and the green reference baselines, the
frontier line, the scoring bounds), the leaderboard, and the paged submissions feed. It
reads one scoring pass at a time, so the three sections always agree, and refreshes every
60 seconds. It has no build step and no dependencies.

The page calls `/v1/competitions/...` on its own origin. `nginx.conf` serves it on port 80
and forwards only `GET` under that prefix to the API on `127.0.0.1:8000`, so the browser
needs no CORS and the page cannot submit anything.

## Install on the API host

```bash
sudo mkdir -p /var/www/competition-dashboard
sudo cp deploy/competition-dashboard/index.html /var/www/competition-dashboard/
sudo cp deploy/competition-dashboard/nginx.conf /etc/nginx/sites-available/competition-dashboard
sudo ln -sf /etc/nginx/sites-available/competition-dashboard /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

The site is the `default_server` on port 80; remove that flag if the host already serves
another site there. All API traffic reaches the platform from nginx's address, so every
viewer shares one per-IP rate-limit budget (120 requests a minute by default).
