# back_end/python_scripts/generate_image.py
import os
import argparse
from typing import Optional


def generate(
    prompt: str,
    reference_path: Optional[str],
    output_path: str,
    width: Optional[int],
    height: Optional[int],
    num_inference_steps: int,
    guidance_scale: float,
    seed: Optional[int],
):
    import torch
    from diffusers import FluxKontextPipeline, FluxPipeline
    from PIL import Image

    dtype = torch.bfloat16
    generator = torch.Generator(device="cpu").manual_seed(seed) if seed is not None else None

    if reference_path:
        pipe = FluxKontextPipeline.from_pretrained(
            "black-forest-labs/FLUX.1-Kontext-dev",
            torch_dtype=dtype,
        )
        pipe.enable_model_cpu_offload()

        ref_image = Image.open(reference_path).convert("RGB")
        w = width if width else (ref_image.width // 16) * 16
        h = height if height else (ref_image.height // 16) * 16

        image = pipe(
            image=ref_image,
            prompt=prompt,
            guidance_scale=guidance_scale,
            num_inference_steps=num_inference_steps,
            height=h,
            width=w,
            generator=generator,
        ).images[0]
    else:
        pipe = FluxPipeline.from_pretrained(
            "black-forest-labs/FLUX.1-dev",
            torch_dtype=dtype,
        )
        pipe.enable_model_cpu_offload()

        image = pipe(
            prompt=prompt,
            width=width or 1024,
            height=height or 1024,
            num_inference_steps=num_inference_steps,
            guidance_scale=guidance_scale,
            generator=generator,
        ).images[0]

    image.save(output_path)
    print(f"Image saved to {output_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", type=str, required=True)
    parser.add_argument("--reference-secret", type=str, default="")
    parser.add_argument("--width", type=int, default=None)
    parser.add_argument("--height", type=int, default=None)
    parser.add_argument("--steps", type=int, default=28)
    parser.add_argument("--guidance-scale", type=float, default=2.5)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    import magnus
    from magnus.http_download import download_file

    output_path = "/tmp/generated_image.png"

    reference_path = None
    if args.reference_secret:
        reference_path = str(download_file(args.reference_secret, "/tmp/reference_input"))

    generate(
        prompt=args.prompt,
        reference_path=reference_path,
        output_path=output_path,
        width=args.width,
        height=args.height,
        num_inference_steps=args.steps,
        guidance_scale=args.guidance_scale,
        seed=args.seed,
    )

    secret = magnus.custody_file(output_path, expire_minutes=1440)

    result_path = os.environ["MAGNUS_RESULT"]
    with open(result_path, "w", encoding="utf-8") as f:
        f.write(f"Image generated successfully.\nPrompt: {args.prompt}\nDownload: magnus receive {secret}")

    action_path = os.environ["MAGNUS_ACTION"]
    with open(action_path, "w", encoding="utf-8") as f:
        f.write(f"magnus receive {secret}\n")


if __name__ == "__main__":
    main()
