MAX_RESPONSE_BYTES = 2 * 1024 * 1024
# 索引文档的上限，与公共索引一致：它是这份协议里唯一可能很大的响应。
MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
# 协议给归档的上限：下载按块落盘，这个数只用来挡住超限的响应。
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
SUPPORTED_PROTOCOL_VERSION = 3
