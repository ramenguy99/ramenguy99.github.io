import os
import glob
from pathlib import Path
from PIL import Image

SIZE = 400

for t in glob.glob("content/ramen/**/thumbnail.*"):
    post_name = Path(t).parent.name
    img: Image.Image = Image.open(t)
    if img.width < img.height:
        img = img.resize((SIZE, int(SIZE * (img.height / img.width))), resample=Image.Resampling.BICUBIC)
        delta = (img.height - 400) / 2
        if post_name == "shoyu_zurich":
            delta += SIZE * 0.11
        img = img.crop((0, delta, SIZE, delta + SIZE))
    else:
        img = img.resize((int(SIZE * (img.width / img.height)), SIZE), resample=Image.Resampling.BICUBIC)
        delta = (img.width - 400) / 2
        if post_name == "tantanmen":
            delta -= SIZE * 0.17
        elif post_name == "tonkotsu":
            delta -= SIZE * 0.25
        img = img.crop((delta, 0, delta + SIZE, SIZE))
    # print(t, f"[{img.width}]x[{img.height}]")
    name, ext = os.path.splitext(t)
    img.save(f"{name}_small{ext}")

