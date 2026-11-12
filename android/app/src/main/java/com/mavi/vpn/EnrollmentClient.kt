package com.mavi.vpn

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.concurrent.TimeUnit

internal sealed interface EnrollmentRedeemResult {
    data class Success(
        val profile: EnrollmentProfile,
    ) : EnrollmentRedeemResult

    data class Failure(
        val message: String,
    ) : EnrollmentRedeemResult
}

internal object EnrollmentClient {
    private const val MAX_RESPONSE_BYTES = 64L * 1024L
    private val jsonMediaType = "application/json; charset=utf-8".toMediaType()

    private val httpClient: OkHttpClient by lazy {
        OkHttpClient
            .Builder()
            .callTimeout(20, TimeUnit.SECONDS)
            .connectTimeout(20, TimeUnit.SECONDS)
            .readTimeout(20, TimeUnit.SECONDS)
            .writeTimeout(20, TimeUnit.SECONDS)
            .followRedirects(false)
            .followSslRedirects(false)
            .build()
    }

    suspend fun redeem(request: EnrollmentRequest): EnrollmentRedeemResult =
        withContext(Dispatchers.IO) {
            val httpRequest =
                Request
                    .Builder()
                    .url(request.redeemUrl)
                    .header("Accept", "application/json")
                    .post(request.requestJson().toRequestBody(jsonMediaType))
                    .build()
            try {
                httpClient.newCall(httpRequest).execute().use { response ->
                    if (!response.isSuccessful) {
                        return@withContext EnrollmentRedeemResult.Failure(httpError(response.code))
                    }
                    if (response.body.contentLength() > MAX_RESPONSE_BYTES) {
                        return@withContext EnrollmentRedeemResult.Failure("Enrollment response is too large")
                    }
                    val source = response.body.source()
                    source.request(MAX_RESPONSE_BYTES + 1)
                    if (source.buffer.size > MAX_RESPONSE_BYTES) {
                        return@withContext EnrollmentRedeemResult.Failure("Enrollment response is too large")
                    }
                    val profile =
                        try {
                            EnrollmentProfile.parse(source.readString(Charsets.UTF_8))
                        } catch (_: EnrollmentValidationException) {
                            return@withContext EnrollmentRedeemResult.Failure(
                                "Enrollment server returned an invalid profile",
                            )
                        }
                    EnrollmentRedeemResult.Success(profile)
                }
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                EnrollmentRedeemResult.Failure(
                    "Could not complete enrollment. The ticket may have been redeemed; request a new link before retrying.",
                )
            }
        }

    private fun httpError(statusCode: Int): String =
        when (statusCode) {
            404, 410 -> "Enrollment link is invalid, expired, or already used"
            429 -> "Too many enrollment attempts; try again later"
            else -> "Enrollment server rejected the request"
        }
}
