#!/usr/bin/env bash
# Render every processed crop as one contact sheet.
#
# Numbers cannot tell you whether a crop is right. Confidence said 0.99 on a card back that was
# cropped inside its own border, and a border-contrast metric said the opposite because a dark
# blue border on a black mat has low contrast whatever the crop. Looking at twenty thumbnails
# settles in seconds what an afternoon of metrics could not.
set -euo pipefail

OUT="${1:-data/contact-sheet.jpg}"
docker compose run --rm --user root -e SHEET_KIND="${SHEET_KIND:-processed}" \
  -v "$(pwd)/data/images:/imgs:ro" \
  -v "$(pwd)/data:/out" \
  api python -c "
import cv2, numpy as np, os
KIND = os.environ.get('SHEET_KIND','processed')
SIDE_A, SIDE_B = 'front','back'
tiles=[]
for sku in sorted(d for d in os.listdir('/imgs') if d.startswith('CARD-')):
    for side in (SIDE_A, SIDE_B):
        img = cv2.imread(f'/imgs/{sku}/{KIND}-{side}.jpg')
        tile = np.full((300,215,3),40,np.uint8) if img is None else cv2.resize(img,(215,300))
        if img is None:
            cv2.putText(tile,'none',(70,160),cv2.FONT_HERSHEY_SIMPLEX,0.7,(80,80,200),2)
        cv2.rectangle(tile,(0,0),(214,299),(0,140,255),2)
        cv2.putText(tile,f'{sku[-3:]}{side[0]}',(6,20),cv2.FONT_HERSHEY_SIMPLEX,0.55,(0,255,255),2)
        tiles.append(tile)
rows=[]
for i in range(0,len(tiles),5):
    row=tiles[i:i+5]
    while len(row)<5: row.append(np.full((300,215,3),20,np.uint8))
    rows.append(np.hstack(row))
if rows:
    cv2.imwrite(f'/out/contact-sheet-{KIND}.jpg', np.vstack(rows), [int(cv2.IMWRITE_JPEG_QUALITY),92])
    print(f'{len(tiles)} crops')
"
echo "Wrote ${OUT}"
