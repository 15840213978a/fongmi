"""Restore the 5.6.6 Java/JNI bridge surface that can be reproduced safely.

The official 5.6.6 APK exposes exact JNI entry points for NativeUdfFileSystem
and FfmpegDolbyVisionP5Native. This script recreates only those Java bridge
classes and wires them conservatively into the public Media3 source:

* Native UDF is used only when the existing Java UDF and ISO9660 readers fail.
* Dolby Vision P5 invokes the official native capability probe for device
  diagnostics while keeping the existing source-built FFmpeg mapping policy.

It deliberately does not reconstruct the larger IsoNavigationSession state
machine from incomplete decompilation.
"""

import argparse
from pathlib import Path

NATIVE_UDF = r'''/*
 * Copyright (C) 2026 The Android Open Source Project
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 */
package androidx.media3.extractor.iso.udf;

import androidx.annotation.Nullable;
import androidx.media3.common.CacheDataReader;
import androidx.media3.extractor.iso.IsoFileEntry;
import java.io.Closeable;
import java.io.IOException;
import java.util.Arrays;
import java.util.List;

/**
 * JNI-backed UDF reader compatible with the FongMi 5.6.6 isoJNI binary.
 *
 * <p>The class and native method names are intentionally stable because the
 * native library exports name-based JNI symbols for this exact package.
 */
final class NativeUdfFileSystem implements Closeable {

  public static final class UnsupportedImageException extends IOException {
    public UnsupportedImageException(String message) {
      super(message);
    }
  }

  private static final boolean AVAILABLE;

  static {
    boolean available;
    try {
      System.loadLibrary("isoJNI");
      available = true;
    } catch (UnsatisfiedLinkError error) {
      available = false;
    }
    AVAILABLE = available;
  }

  private long nativeHandle;

  public static boolean isAvailable() {
    return AVAILABLE;
  }

  public NativeUdfFileSystem(CacheDataReader reader) throws IOException {
    if (!AVAILABLE) {
      throw new UnsupportedImageException("ISO native library is unavailable");
    }
    nativeHandle = nativeOpen(reader);
    if (nativeHandle == 0) {
      throw new UnsupportedImageException("Could not open native UDF image");
    }
  }

  @Nullable
  public synchronized IsoFileEntry findFile(String path) throws IOException {
    ensureOpen();
    return nativeFindFile(nativeHandle, path);
  }

  public synchronized List<String> listFiles(String path) throws IOException {
    ensureOpen();
    String[] files = nativeListFiles(nativeHandle, path);
    return files == null ? java.util.Collections.emptyList() : Arrays.asList(files);
  }

  @Override
  public synchronized void close() {
    if (nativeHandle != 0) {
      nativeClose(nativeHandle);
      nativeHandle = 0;
    }
  }

  private void ensureOpen() throws IOException {
    if (nativeHandle == 0) {
      throw new IOException("UDF image is closed");
    }
  }

  private static native long nativeOpen(CacheDataReader reader);

  private static native String[] nativeListFiles(long handle, String path);

  @Nullable
  private static native IsoFileEntry nativeFindFile(long handle, String path);

  private static native void nativeClose(long handle);
}
'''

DOVI = r'''/*
 * Copyright (C) 2026 The Android Open Source Project
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 */
package androidx.media3.decoder.ffmpeg;

import android.os.Build;
import android.view.Surface;
import androidx.annotation.Nullable;
import androidx.media3.common.util.Log;

/**
 * Java/JNI surface for the FongMi 5.6.6 Dolby Vision profile-5 native mapper.
 *
 * <p>The class name and all native signatures must stay stable because the
 * native library registers this complete table from JNI_OnLoad.
 */
final class FfmpegDolbyVisionP5Native {

  private static final String TAG = "FfmpegDoviP5Native";
  private static final int REQUIRED_CAPABILITIES = 0x7F;

  @Nullable private static Probe cachedProbe;

  static final class Probe {
    public final boolean available;
    public final int capabilities;
    public final String reason;

    Probe(int capabilities, String reason, boolean available) {
      this.available = available;
      this.capabilities = capabilities;
      this.reason = reason;
    }

    @Override
    public String toString() {
      return "Probe(available="
          + available
          + ", capabilities="
          + capabilities
          + ", reason="
          + reason
          + ")";
    }
  }

  static final class Stats {
    public long acquired;
    public long matched;
    public long unmatched;
    public long frameDrops;
    public long pendingFrames;
    public long parsedRpus;
    public long malformedRpus;
    public long rpuDrops;
    public long pendingRpus;
    public long rendered;
    public long failures;

    private final long[] values = new long[11];

    private boolean update(long handle) {
      if (!nativeGetStats(handle, values)) {
        return false;
      }
      acquired = values[0];
      matched = values[1];
      unmatched = values[2];
      frameDrops = values[3];
      pendingFrames = values[4];
      parsedRpus = values[5];
      malformedRpus = values[6];
      rpuDrops = values[7];
      pendingRpus = values[8];
      rendered = values[9];
      failures = values[10];
      return true;
    }

    @Override
    public String toString() {
      return "Stats(acquired="
          + acquired
          + ", matched="
          + matched
          + ", unmatched="
          + unmatched
          + ", frameDrops="
          + frameDrops
          + ", pendingFrames="
          + pendingFrames
          + ", parsedRpus="
          + parsedRpus
          + ", malformedRpus="
          + malformedRpus
          + ", rpuDrops="
          + rpuDrops
          + ", pendingRpus="
          + pendingRpus
          + ", rendered="
          + rendered
          + ", failures="
          + failures
          + ")";
    }
  }

  static final class NativeRenderer implements AutoCloseable {
    private long handle;
    @Nullable private Surface inputSurface;
    private final Stats stats;

    private NativeRenderer(long handle, Surface inputSurface) {
      this.handle = handle;
      this.inputSurface = inputSurface;
      this.stats = new Stats();
    }

    public synchronized Surface getInputSurface() {
      if (inputSurface == null) {
        throw new IllegalStateException("Dolby Vision renderer is closed");
      }
      return inputSurface;
    }

    public synchronized void setOutputSurface(
        @Nullable Surface surface, int width, int height) {
      if (handle != 0) {
        nativeSetOutputSurface(handle, surface, width, height);
      }
    }

    public synchronized boolean queueFrame(long presentationTimeUs, long releaseTimeNs) {
      return handle != 0 && nativeQueueFrame(handle, presentationTimeUs, releaseTimeNs);
    }

    public synchronized boolean queueRpu(long presentationTimeUs, byte[] rpu) {
      return handle != 0 && nativeQueueRpu(handle, presentationTimeUs, rpu);
    }

    public synchronized void clear() {
      if (handle != 0) {
        nativeClear(handle);
      }
    }

    public synchronized void redraw() {
      if (handle != 0) {
        nativeRedraw(handle);
      }
    }

    public synchronized Stats getStats() {
      if (handle != 0) {
        stats.update(handle);
      }
      return stats;
    }

    @Override
    public synchronized void close() {
      if (handle == 0) {
        return;
      }
      Surface surface = inputSurface;
      inputSurface = null;
      nativeRelease(handle);
      handle = 0;
      if (surface != null) {
        surface.release();
      }
    }
  }

  static synchronized Probe probe() {
    if (cachedProbe != null) {
      return cachedProbe;
    }
    if (Build.VERSION.SDK_INT < 26) {
      return cacheProbe(new Probe(0, "api-below-26", false));
    }
    if (!FfmpegLibrary.isAvailable()) {
      return cacheProbe(new Probe(0, "ffmpeg-unavailable", false));
    }
    try {
      System.loadLibrary("ffmpegDoviJNI");
      int capabilities = nativeProbeCapabilities();
      boolean available = hasRequiredCapabilities(capabilities);
      return cacheProbe(
          new Probe(
              capabilities, available ? "available" : "missing-capability", available));
    } catch (LinkageError | RuntimeException error) {
      return cacheProbe(
          new Probe(0, "probe-" + error.getClass().getSimpleName(), false));
    }
  }

  static boolean hasRequiredCapabilities(int capabilities) {
    return (capabilities & REQUIRED_CAPABILITIES) == REQUIRED_CAPABILITIES;
  }

  @Nullable
  static NativeRenderer create(int width, int height) {
    if (width <= 0 || height <= 0 || !probe().available) {
      return null;
    }
    long handle = nativeCreate(width, height);
    if (handle == 0) {
      return null;
    }
    Surface inputSurface = nativeGetInputSurface(handle);
    if (inputSurface == null) {
      nativeRelease(handle);
      return null;
    }
    return new NativeRenderer(handle, inputSurface);
  }

  private static Probe cacheProbe(Probe probe) {
    cachedProbe = probe;
    Log.i(TAG, "GPU mapping " + probe);
    return probe;
  }

  private static native void nativeClear(long handle);

  private static native long nativeCreate(int width, int height);

  private static native Surface nativeGetInputSurface(long handle);

  private static native boolean nativeGetStats(long handle, long[] stats);

  private static native int nativeProbeCapabilities();

  private static native boolean nativeQueueFrame(
      long handle, long presentationTimeUs, long releaseTimeNs);

  private static native boolean nativeQueueRpu(long handle, long presentationTimeUs, byte[] rpu);

  private static native void nativeRedraw(long handle);

  private static native void nativeRelease(long handle);

  private static native void nativeSetOutputSurface(
      long handle, @Nullable Surface surface, int width, int height);

  private FfmpegDolbyVisionP5Native() {}
}
'''

def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--media3-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.media3_root.resolve()

    udf_native = (
        root
        / "libraries"
        / "extractor"
        / "src"
        / "main"
        / "java"
        / "androidx"
        / "media3"
        / "extractor"
        / "iso"
        / "udf"
        / "NativeUdfFileSystem.java"
    )
    udf_native.write_text(NATIVE_UDF, encoding="utf-8")

    udf = udf_native.with_name("UdfFileSystem.java")
    text = udf.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "  @Nullable private Iso9660FileSystem iso9660Fallback;\n",
        "  @Nullable private Iso9660FileSystem iso9660Fallback;\n"
        "  private boolean nativeFallback;\n",
        "UDF field",
    )
    text = replace_once(
        text,
        """    iso9660Fallback = null;
    try {
      findFsdAndPartitionBase();
    } catch (IOException udfError) {
      try {
        iso9660Fallback = Iso9660FileSystem.open(reader);
      } catch (IOException isoError) {
        udfError.addSuppressed(isoError);
        throw udfError;
      }
    }
""",
        """    iso9660Fallback = null;
    nativeFallback = false;
    try {
      findFsdAndPartitionBase();
    } catch (IOException udfError) {
      try {
        iso9660Fallback = Iso9660FileSystem.open(reader);
      } catch (IOException isoError) {
        try (NativeUdfFileSystem ignored = new NativeUdfFileSystem(reader)) {
          nativeFallback = true;
        } catch (IOException nativeError) {
          udfError.addSuppressed(isoError);
          udfError.addSuppressed(nativeError);
          throw udfError;
        }
      }
    }
""",
        "UDF open",
    )
    text = replace_once(
        text,
        """  public IsoFileEntry findFile(String path) throws IOException {
    if (iso9660Fallback != null) {
      return iso9660Fallback.findFile(path);
    }
""",
        """  public IsoFileEntry findFile(String path) throws IOException {
    if (nativeFallback) {
      try (NativeUdfFileSystem nativeFileSystem = new NativeUdfFileSystem(reader)) {
        return nativeFileSystem.findFile(path);
      }
    }
    if (iso9660Fallback != null) {
      return iso9660Fallback.findFile(path);
    }
""",
        "UDF findFile",
    )
    text = replace_once(
        text,
        """  public List<String> listFiles(String dirPath) throws IOException {
    if (iso9660Fallback != null) {
      return iso9660Fallback.listFiles(dirPath);
    }
""",
        """  public List<String> listFiles(String dirPath) throws IOException {
    if (nativeFallback) {
      try (NativeUdfFileSystem nativeFileSystem = new NativeUdfFileSystem(reader)) {
        return nativeFileSystem.listFiles(dirPath);
      }
    }
    if (iso9660Fallback != null) {
      return iso9660Fallback.listFiles(dirPath);
    }
""",
        "UDF listFiles",
    )
    udf.write_text(text, encoding="utf-8")

    dovi = (
        root
        / "libraries"
        / "decoder_ffmpeg"
        / "src"
        / "main"
        / "java"
        / "androidx"
        / "media3"
        / "decoder"
        / "ffmpeg"
        / "FfmpegDolbyVisionP5Native.java"
    )
    dovi.write_text(DOVI, encoding="utf-8")

    library = dovi.with_name("FfmpegLibrary.java")
    text = library.read_text(encoding="utf-8")
    text = replace_once(
        text,
        """      case MediaCodecInfo.CodecProfileLevel.DolbyVisionProfileDvheStn: // Profile 5.
        return canAttemptMapping
            ? DOLBY_VISION_OUTPUT_MODE_REQUIRE_MAPPING
            : DOLBY_VISION_OUTPUT_MODE_NONE;
""",
        """      case MediaCodecInfo.CodecProfileLevel.DolbyVisionProfileDvheStn: // Profile 5.
        if (canAttemptMapping) {
          // Probe the exact 5.6.6 JNI bridge on-device. The existing source-built FFmpeg
          // path remains responsible for playback until the renderer/RPU handoff is wired.
          FfmpegDolbyVisionP5Native.probe();
        }
        return canAttemptMapping
            ? DOLBY_VISION_OUTPUT_MODE_REQUIRE_MAPPING
            : DOLBY_VISION_OUTPUT_MODE_NONE;
""",
        "Dolby Vision profile 5",
    )
    library.write_text(text, encoding="utf-8")

    print("Restored NativeUdfFileSystem JNI bridge")
    print("Restored FfmpegDolbyVisionP5Native JNI bridge and P5 capability probe")


if __name__ == "__main__":
    main()
