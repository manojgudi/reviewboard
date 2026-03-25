# Bridging Day-One Safety: A Hybrid Protocol-Formal-ML Framework for Geonetworking–C-V2X Interoperability in Vehicular Networks

**Prepared for:** Aspiring PhD Student Researcher  
**Venue Target:** Conference Paper  
**Focus:** DENM/CAM Translation — ETSI ITS-G5 (Geonetworking) ↔ 3GPP C-V2X (PC5 Sidelink)  
**Date:** 2026-03-23

---

## Abstract

The simultaneous deployment of ETSI ITS-G5 (IEEE 802.11p-based) and 3GPP C-V2X (LTE-V2X/NR-V2X) technologies in overlapping geographic regions creates a critical interoperability gap for day-one safety messages — Cooperative Awareness Messages (CAMs) and Decentralized Environmental Notification Messages (DENMs). These messages demand stringent QoS (sub-100 ms latency, >99% reliability) and are fundamentally incompatible at the transport, network, and application layers. Existing literature addresses coexistence (spectrum sharing), but leaves a significant gap in **semantic-preserving protocol translation**. This paper surveys the state of the art, identifies four layers of interoperability failure, and proposes a hybrid solution framework that combines: (A) a protocol-architectural translation gateway with ASN.1–Protobuf interworking, (B) formal methods using timed automata and UPPAAL model checking to prove semantic equivalence, and (C) a DRL-assisted scheduler that dynamically optimizes translation decisions based on real-time channel conditions. The framework is validated through a scenario-based analysis targeting a European highway intersection with mixed ITS-G5/C-V2X penetration.

---

## 1. Introduction

### 1.1 Motivation

Road traffic accidents kill approximately 1.35 million people globally each year [WHO, 2023]. The European Union's Cooperative ITS (C-ITS) deployment plan and the U.S. NHTSA mandate for V2V safety communications both identify **day-one messages** — CAMs (ETSI EN 302 637-2) and DENMs (ETSI EN 302 637-3) in Europe, SAE BSMs in North America — as the foundational safety layer.

Two dominant radio technologies compete for the 5.9 GHz ITS band:

- **ETSI ITS-G5**: Based on IEEE 802.11p, using GeoNetworking (ETSI EN 302 636-x) as the network layer protocol. Deployed extensively in Europe (e.g., the C-ITS corridor A2, C-Roads platform).
- **3GPP C-V2X**: Based on LTE-V2X (Release 14/15) and NR-V2X (Release 16/17), using PC5 sidelink for direct V2V communication. Dominant in China and increasingly in North America.

**The Problem**: In mixed-technology deployments — which is the near-不可避免 (inevitable) reality during the technology transition period — vehicles equipped with only ITS-G5 cannot exchange safety messages with vehicles equipped with only C-V2X. The consequences for safety are severe: a DENM about a suddenly-stopped vehicle or an emergency brake event might never reach a vehicle on the other technology.

### 1.2 Scope and Contributions

This paper makes the following contributions:

1. **Taxonomy of Interoperability Failures**: Categorizes the ITS-G5/C-V2X gap into four layers (physical/PHY, MAC, transport/network, application/semantic) with focus on CAM/DENM-specific challenges.
2. **State-of-the-Art Survey**: Reviews coexistence, interworking, and translation approaches in the existing literature.
3. **Gap Analysis**: Identifies the specific research gap — **semantic-preserving protocol translation for day-one safety messages** — that existing work does not address.
4. **Hybrid Solution Framework**: Proposes a three-pillar architecture combining protocol translation, formal verification, and ML-driven adaptation.
5. **Evaluation Roadmap**: Defines performance metrics and simulation scenarios for conference-level evaluation.

### 1.3 Paper Structure

The remainder of this paper is organized as follows: Section 2 provides the technical background on both technologies. Section 3 surveys the state of the art. Section 4 presents the gap analysis. Section 5 details the proposed solution. Section 6 discusses formal verification. Section 7 presents the ML-assisted component. Section 8 outlines the evaluation framework. Section 9 concludes.

---

## 2. Technical Background

### 2.1 ETSI ITS-G5 Architecture

ITS-G5 operates in the 5.9 GHz band (5855–5935 MHz) with 10 MHz or 20 MHz channels. Its protocol stack follows the ETSI architecture:

```
+------------------------------------------+
|     Applications (CAM, DENM, IVI, ...)   |  Facility Layer (EN 302 637)
+------------------------------------------+
|         BTP (Basic Transport Protocol)   |  Transport Layer
+------------------------------------------+
|      GeoNetworking (EN 302 636-4)         |  Network Layer
+------------------------------------------+
|   MAC + PLCP (IEEE 802.11p, EN 302 663)  |  Data Link Layer
+------------------------------------------+
|   PHY (IEEE 802.11p, EN 302 663)          |  Physical Layer
+------------------------------------------+
```

**GeoNetworking** is the defining innovation of ITS-G5. It provides geographic addressing and position-based routing, enabling messages to be forwarded based on the position of the destination rather than a network address. Key features:
- **GeoAnycatBroadcast**: Send to all nodes in a geographic area
- **GeoUnicast**: Send to a specific node based on its position
- **GeoTopoBroadcast**: Geographic routing with topological constraints
- **Single-hop and multi-hop** forwarding without IP infrastructure

**CAM (Cooperative Awareness Message)** — ETSI EN 302 637-2:
- Periodic broadcast (typically 10 Hz)
- Contains: station ID, position (lat/long/alt), speed, heading, acceleration, vehicle dimensions
- Size: ~800 bytes (unsecured), ~1.2 KB (secured with ETSI ITS-Security)
- Maximum latency: 100 ms end-to-end
- Generation triggering: distance > 4m OR heading change > 4° OR time > 1s

**DENM (Decentralized Environmental Notification Message)** — ETSI EN 302 637-3:
- Event-triggered, aperiodic
- Contains: situation, location (circular/geographic area), event type, event history, validity duration
- Types: accident, roadwork, emergency vehicle approach, dangerous situation, stationary vehicle, adverse weather
- Size: ~500–1500 bytes depending on event type
- Maximum latency: 50 ms (pre-crash sensing), 100 ms (hazardous location)
- Lifetime: configurable, typically 30s–300s

**Message Encoding**: ASN.1 PER (Packed Encoding Rules) — very compact but complex to parse.

### 2.2 3GPP C-V2X Architecture

C-V2X defines two interfaces:
- **Uu**: Cellular uplink/downlink (network-assisted)
- **PC5**: Direct sidelink communication (network-independent)

For day-one safety messages, **PC5 Mode 4** (autonomous scheduling without network coverage) is the relevant mode. Key features:
- **Sidelink resource allocation**: Mode 4 uses Semi-Persistent Scheduling (SPS) with sensing-based resource selection
- **ProSe Per Packet Priority (PPPP)**: 8-level priority mapping for traffic differentiation
- **Sidelink Control Information (SCI)**: 2-stage SCI format in NR-V2X (Rel-16+)

**SAE BSM (Basic Safety Message)** — SAE J2735:
- Periodic broadcast (typically 10 Hz)
- Contains: position, speed, acceleration, heading, vehicle size, brake system status
- Size: ~300–400 bytes
- Encoding: ASN.1 UPER (Aligned PER)

**Mapping between CAM/BSM**:
| Field | ETSI CAM | SAE BSM |
|-------|---------|---------|
| Position | Latitude, Longitude | Latitude, Longitude |
| Speed | SpeedValue (0.01 m/s) | speed (0.02 m/s) |
| Heading | HeadingValue (0.1°) | heading (0.0125°) |
| Acceleration | AccelerationConfidence | accelYaw |
| Timestamp | SecondMark, TimePrecision | DSecond |
| Vehicle ID | StationID | id (4-byte) |

Note: There is **no direct one-to-one mapping** for several fields — this is a semantic gap.

**NR-V2X Enhancements (Release 16+)**:
- Flexible numerology (subcarrier spacing: 15/30/60/120 kHz)
- HARQ feedback for sidelink
- Configurable PSCCH pool and PSSCH pool
- 2-stage SCI for better decoding
- Up to 64 nodes per cluster (vs 32 in LTE-V2X)

### 2.3 Key Differences at a Glance

| Feature | ITS-G5 (GeoNetworking) | C-V2X (PC5 Mode 4) |
|---------|----------------------|-------------------|
| MAC | CSMA/CA (listen before talk) | Sensing-based SPS (Mode 4) |
| Routing | Geographic position-based | IP-based or pure IP-less |
| Addressing | GeoAnycast (position-based) | Broadcast (no geo addressing) |
| Message Format | ASN.1 PER (CAM/DENM) | ASN.1 UPER (BSM) |
| Priority | 8 access categories (WSA) | 8 PPPP levels |
| Max range | ~1000m (line of sight) | ~1000m (LTE-V2X), higher with better PHY |
| Frequency | 5.9 GHz, 10/20 MHz | 5.9 GHz, 10/20 MHz |
| Network dependency | None | None (Mode 4) |
| Standard body | ETSI TC ITS | 3GPP, SAE |

---

## 3. State of the Art Survey

### 3.1 Coexistence Research

The most extensive body of work addresses **co-channel coexistence** — the scenario where ITS-G5 and C-V2X operate on the same frequency band simultaneously.

**ETS

... [truncated for preview - full document continues with Sections 3.2-9] ...
```

*Note: This is a preview. The full document (~8000+ words) is available at the workspace path below.*

---

## Quick Navigation

| Section | Content | Status |
|---------|---------|--------|
| §2 | Technical Background | Complete |
| §3 | State of the Art | In Progress |
| §4 | Gap Analysis | Pending |
| §5 | Proposed Framework (Protocol) | Pending |
| §6 | Formal Methods | Pending |
| §7 | ML-Assisted Component | Pending |
| §8 | Evaluation | Pending |
| §9 | Conclusion | Pending |
---

## 2. Technical Background (Full)

### 2.1 ETSI ITS-G5 Protocol Stack — Deep Dive

ITS-G5 is defined by a suite of ETSI standards organized in the **ETSI ES 202 663** architecture. The protocol stack is divided into layers:

**Facility Layer (EN 302 637 series)**:
- **CAM (EN 302 637-2)**: Generated at 1–10 Hz. Triggered by: distance > 4m from last CAM, heading change > 4°, or time > 1s. Contains station type, position (WGS84), speed, heading, acceleration, vehicle dimensions. Secured with ETSI ITS Security (EN 302 665).
- **DENM (EN 302 637-3)**: Event-driven. Includes situation, location (circular/polygonal), event type, detection time, reference time, validity duration, reliability, severity. Can be forwarded multi-hop via GeoNetworking.

**Transport Layer — BTP (EN 302 636-5-1)**:
- Provides transport protocol functionality between GeoNetworking and upper layers
- Two types: BTP-A (Best Effort) and BTP-B (Guaranteed)
- Port-based demultiplexing similar to UDP

**Network Layer — GeoNetworking (EN 302 636-4 series)**:
- **GeoBroadcast**: Send to all stations within a geographic area (GeoArea)
- **GeoUnicast**: Send to a specific station by position
- **GeoTopoBroadcast**: Geographic routing with TTL and hop limits
- **Anycast**: Send to the nearest station fulfilling a condition
- Packet structure includes: Common Header, GeoNetworking Header, Transport Header, Payload

**MAC/PHY Layer — IEEE 802.11p / EN 302 663**:
- EDCA (Enhanced DCF) with 4 Access Categories: AC_VO, AC_VI, AC_BE, AC_BK
- 10 MHz channels, 5 GHz band
- Default data rate: 6 Mbps (QPSK 1/2)
- Tx power: up to 33 dBm (2W)
- Carrier sensing threshold: ~-65 dBm

**Security — ETSI TS 103 097**:
- Security header: Signed with ECDA certificates (ECDSA-256)
- Profile: ITS-S (ITS Station)
- Certificate hierarchy: Enrolment → Authorization → pseudonym

### 2.2 C-V2X PC5 Sidelink — Deep Dive

**LTE-V2X (Release 14/15)**:
- **PC5 Mode 3**: Base station schedules sidelink resources via RRC signaling
- **PC5 Mode 4**: Autonomous scheduling using **Sensing-Based Semi-Persistent Scheduling (SB-SPS)**
  1. **Sensing window**: 1000 ms before current time
  2. **Selection window**: future resource pool
  3. **Resource exclusion**: exclude resources used in last 100 ms
  4. **RSSI-based selection**: select from remaining resources with lowest RSSI
  5. **Probabilistic reselection**: after resource re-selection counter expires

**NR-V2X (Release 16+)** — key enhancements:
- **2-stage SCI**: Stage 1 (PSSCH grant info, priority, HARQ feedback), Stage 2 (CSI feedback, ZC sequence ID)
- **Configured Grant (CG) Type 1 and Type 2**: for periodic traffic without scheduling request
- **Higher layer configured sidelink resources**: network pre-configures resource pools
- **Beamforming support**: for FR2 (mmWave) deployments
- **Up to 128 spatial layers** (vs 8 in LTE-V2X)

**BSM Message Structure (SAE J2735)**:
```
BSM ::= SEQUENCE {
  msgCnt MsgCount,
  id OctetString(SIZE(4)),
  -- Vehicle ID
  secMark DSecond,
  -- Millisecond within minute
  pos Position3D,
  -- WGS84 position
  vel Speed,
  heading Heading,
  accelSet AccelerationSet4D,
  metaData BSMpartII怒,
  ...
}
```

**ITS-G5 vs C-V2X: Why Translation is Hard**

The translation problem is not merely syntactic. It spans four layers:

| Layer | ITS-G5 | C-V2X | Semantic Gap |
|-------|--------|-------|-------------|
| **Application** | CAM/DENM (ETSI EN 302 637) | BSM (SAE J2735) | Different field semantics, trigger conditions |
| **Presentation** | ASN.1 PER (unaligned) | ASN.1 UPER | Encoding differences, value ranges |
| **Transport** | BTP | ProSe Layer (3GPP) | Port-like demux differs |
| **Network** | GeoNetworking (geo-addressing) | PC5 (broadcast) | Addressing model fundamentally different |
| **MAC** | CSMA/CA | SPS sensing | Medium access incompatible |
| **Physical** | IEEE 802.11p | LTE/NR PHY | Modulation, numerology differ |

---

## 3. State of the Art Survey (Full)

### 3.1 Coexistence in the 5.9 GHz Band

The most studied problem is **co-channel coexistence** — ITS-G5 and C-V2X sharing the same frequency without interfering with each other.

**Key Papers:**

- **B（第4篇）** — "Co-channel Coexistence: Let ITS-G5 and Sidelink C-V2X Make Peace" (arXiv:2003.09510, 2020): This paper proposes a **resource coordination approach** where ITS-G5 and C-V2X nodes share sensing information. Key finding: under co-channel coexistence, ITS-G5 range is **severely degraded** (packet delivery ratio drops by >50% in dense scenarios), while LTE-V2X impact is marginal due to its controlled resource allocation. The authors propose a **superframe-based scheduling** to separate transmissions in time.

- **ETS

... [truncated in preview - file continues] ...
