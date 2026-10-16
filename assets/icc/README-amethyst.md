# Amethyst CMYK ICC Profiles

> [!CAUTION]
> **USE AT YOUR OWN RISK:** These profiles are experimental, and have **NOT** received any practical
> evaluation yet.

> [!IMPORTANT]
> **HARD PROOF:** You can mitigate your own risk somewhat by requesting a hard proof you can inspect
> in person from your print shop in advance of any print run.

## What's The Significance Of "Amethyst" Or "mth"
Nothing in particular, it's a unique marker to reflect where these profiles originated from.

> [!NOTE]
> We kindly request that others who generate their own profiles do not use "Amethyst" to prevent
> any confusion in this matter.

## Why Another Set Of CMYK ICC Profiles
This project aims to provide a consistent set of commonly used CMYK ICC profiles, specifically
including ink limiting variations, as some applications like for example Scribus don't support
arbitrary ink limiting yet.

Keep in mind, that while profiles from this set should give you similar results to similarly named
profiles, they are unlikely to be identical, amongst other things, due to parameterization of
black generation.

## Profile Parameters

| Name              | Value                                                              | Note                                  |
| ----------------- | ------------------------------------------------------------------ | ------------------------------------- |
| Software          | [ArgyllCMS](https://www.argyllcms.com/) >= 3.1.0                   | See Note Below                        |
| Gamut Mapping     | [sRGB](https://en.wikipedia.org/wiki/SRGB)                         | For Perceptual/Saturation Intents     |
| Black Start       | 20%                                                                | Approximately                         |
| Black Generation  | 70%                                                                | Medium                                |
| Smoothing         |  2%                                                                | Medium                                |
| Average Deviation | < 0.3 [CIE DE2000](https://en.wikipedia.org/wiki/Color_difference) | Imperceptible                         |
| Maximum Deviation | < 1.5 [CIE DE2000](https://en.wikipedia.org/wiki/Color_difference) | Perceptible Through Close Observation |

> [!NOTE]
> ArgyllCMS < 3.1.0 had a bug, when compiled with a somewhat recent version of GCC, resulted
> in bumpy black generation, which we want to avoid.

## Other CMYK ICC Profile Sources

- [colormanagement.org](https://www.colormanagement.org)
- [color.org](https://www.color.org/registry/index.xalter)
- [adobe.com](https://www.adobe.com/support/downloads/iccprofiles/iccprofiles_win.html)
