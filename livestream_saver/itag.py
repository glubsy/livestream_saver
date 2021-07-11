# TODO also check https://github.com/pytube/pytube/blob/master/pytube/itags.py
video_height_ranking = {
    "4320": [402, 138, 308],
    "2160": [401, 266],
    "1440": [400, 264],
    "1080": [399, 299, 137],
    "720":  [398, 298, 136],
    "480":  [397, 135],
    "360":  [396, 134],
    "240":  [395, 133],
    "144":  [394, 160]
}
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
