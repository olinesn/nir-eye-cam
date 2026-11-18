# ST's VD55G1 firmware patch

Silicon revision 2 ("cut 2", `0x2020` in register 0x0004) of the VD55G1 only
boots after this firmware patch is written to the sensor. Revision 3 boots
without it. `../vd55g1.c` includes the file and loads it at start-up.

| File | From |
|---|---|
| `vd55g1_patch_cut_2.c` | [STMicroelectronics/stm32-mw-camera](https://github.com/STMicroelectronics/stm32-mw-camera), `sensors/vd55g1/vd55g1_patch_cut_2.c` at commit `e88de17d4adb3144aa31be2b558345acd1184037`, unchanged. Patch version 2.2. |
| `LICENSE.md` | The same directory's `LICENSE.md`: BSD-3-Clause, Copyright STMicroelectronics. It covers `vd55g1_patch_cut_2.c` only; the rest of this repository keeps its own license. |
