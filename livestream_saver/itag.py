# TODO also check https://github.com/pytube/pytube/blob/master/pytube/itags.py
video_height_ranking = {
    "4320": [402, 138, 308, 702],
    "2160": [401, 266, 701],
    "1440": [400, 264, 700],
    "1080": [399, 299, 137, 699],
    "720":  [398, 298, 136, 698],
    "480":  [397, 135, 697],
    "360":  [396, 134, 696],
    "240":  [395, 133, 695],
    "144":  [394, 160, 694]
}

# itag: 308      qualityLabel: 1440p60   mimeType: video/webm; codecs="vp9"      bitrate: 9016000        quality: hd1440 fps: 60
# itag: 299      qualityLabel: 1080p60   mimeType: video/mp4; codecs="avc1.64002a"       bitrate: 6686125        quality: hd1080     fps: 60
# itag: 303      qualityLabel: 1080p60   mimeType: video/webm; codecs="vp9"      bitrate: 3016000        quality: hd1080 fps: 60
# itag: 136      qualityLabel: 720p      mimeType: video/mp4; codecs="avc1.4d401f"       bitrate: 2684050        quality: hd720      fps: 30
# itag: 247      qualityLabel: 720p      mimeType: video/webm; codecs="vp9"      bitrate: 1040000        quality: hd720  fps: 30
# itag: 298      qualityLabel: 720p60    mimeType: video/mp4; codecs="avc1.4d4020"       bitrate: 4018075        quality: hd720      fps: 60
# itag: 302      qualityLabel: 720p60    mimeType: video/webm; codecs="vp9"      bitrate: 1816000        quality: hd720  fps: 60
# itag: 135      qualityLabel: 480p      mimeType: video/mp4; codecs="avc1.4d401f"       bitrate: 1350025        quality: large      fps: 30
# itag: 244      qualityLabel: 480p      mimeType: video/webm; codecs="vp9"      bitrate: 528000 quality: large  fps: 30
# itag: 134      qualityLabel: 360p      mimeType: video/mp4; codecs="avc1.4d401e"       bitrate: 1008250        quality: medium     fps: 30
# itag: 243      qualityLabel: 360p      mimeType: video/webm; codecs="vp9"      bitrate: 292000 quality: medium fps: 30
# itag: 133      qualityLabel: 240p      mimeType: video/mp4; codecs="avc1.4d4015"       bitrate: 456228 quality: small  fps: 30
# itag: 242      qualityLabel: 240p      mimeType: video/webm; codecs="vp9"      bitrate: 166000 quality: small  fps: 30
# itag: 160      qualityLabel: 144p      mimeType: video/mp4; codecs="avc1.42c00b"       bitrate: 212465 quality: tiny   fps: 15
# itag: 278      qualityLabel: 144p      mimeType: video/webm; codecs="vp9"      bitrate: 111000 quality: tiny   fps: 30
# itag: 140      audioQuality: AUDIO_QUALITY_MEDIUM      mimeType: audio/mp4; codecs="mp4a.40.2" bitrate: 144000 audioSampleRate: 44100


# quality_video_ranking = [
#     402, 138,         # 4320p: AV1 HFR | VP9 HFR | H.264
#     401, 266,         # 2160p: AV1 HFR | VP9.2 HDR HFR | VP9 HFR | VP9 | H.264
#     400, 264,         # 1440p: AV1 HFR | VP9.2 HDR HFR | VP9 HFR | VP9 | H.264
#     399, 299, 137,    # 1080p: AV1 HFR | VP9.2 HDR HFR | VP9 HFR | VP9 | H.264 HFR | H.264
#     398, 298, 136,    # 720p: AV1 HFR | VP9.2 HDR HFR | VP9 HFR | VP9 | H.264 HFR | H.264
#     397, 135,           # 480p: AV1 | VP9.2 HDR HFR | VP9 | H.264
#     396, 134,           # 360p: AV1 | VP9.2 HDR HFR | VP9 | H.264
#     395, 133,           # 240p: AV1 | VP9.2 HDR HFR | VP9 | H.264
#     394, 160            # 144p: AV1 | VP9.2 HDR HFR | VP9 | H.264
# ]

# TODO check https://github.com/pytube/pytube/blob/master/pytube/itags.py#L97
quality_audio_ranking = [140]

# DASH_AUDIO = {
#     # DASH Audio
#     139: (None, "48kbps"),  # MP4
#     140: (None, "128kbps"),  # MP4
#     141: (None, "256kbps"),  # MP4
#     171: (None, "128kbps"),  # WEBM
#     172: (None, "256kbps"),  # WEBM
#     249: (None, "50kbps"),  # WEBM
#     250: (None, "70kbps"),  # WEBM
#     251: (None, "160kbps"),  # WEBM
#     256: (None, "192kbps"),  # MP4
#     258: (None, "384kbps"),  # MP4
#     325: (None, None),  # MP4
#     328: (None, None),  # MP4
# }


# Experimental - VP9 support
# quality_video_ranking = [
    # 402, 272, 138, 				# 4320p: AV1 HFR | VP9 HFR | H.264
    # 401, 337, 315, 313, 266,		# 2160p: AV1 HFR | VP9.2 HDR HFR | VP9 HFR | VP9 | H.264
    # 400, 336, 308, 271, 264,		# 1440p: AV1 HFR | VP9.2 HDR HFR | VP9 HFR | VP9 | H.264
    # 399, 335, 303, 248, 299, 137,	# 1080p: AV1 HFR | VP9.2 HDR HFR | VP9 HFR | VP9 | H.264 HFR | H.264
    # 398, 334, 302, 247, 298, 136,	# 720p: AV1 HFR | VP9.2 HDR HFR | VP9 HFR | VP9 | H.264 HFR | H.264
    # 397, 333, 244, 135,			# 480p: AV1 | VP9.2 HDR HFR | VP9 | H.264
    # 396, 332, 243, 134,			# 360p: AV1 | VP9.2 HDR HFR | VP9 | H.264
    # 395, 331, 242, 133, 			# 240p: AV1 | VP9.2 HDR HFR | VP9 | H.264
    # 394, 330, 278, 160			# 144p: AV1 | VP9.2 HDR HFR | VP9 | H.264
# ]
# quality_audio_ranking = [
#     251,                # Opus medium quality
#     250,                # Opus low quality
#     249,                # Opus low quality
#     172,171,141,140,139
# ]
